import AppKit

// Double-tap Command: screenshot the screen under the mouse, bring OpenCode forward, paste it into the message box.

let openCodeID = "ai.opencode.desktop"
let doubleTapWindow: TimeInterval = 0.35

let logURL = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/SnapToOpenCode.log")

func log(_ message: String) {
  let line = "\(Date()) \(message)\n"
  if let handle = try? FileHandle(forWritingTo: logURL) {
    handle.seekToEndOfFile()
    handle.write(line.data(using: .utf8)!)
    try? handle.close()
  } else {
    try? line.write(to: logURL, atomically: true, encoding: .utf8)
  }
}

var commandDown = false
var otherKeyDuringPress = false
var lastTap: TimeInterval = 0
var busy = false

func displayUnderMouse() -> Int {
  let mouse = NSEvent.mouseLocation
  let index = NSScreen.screens.firstIndex { NSMouseInRect(mouse, $0.frame, false) } ?? 0
  return index + 1
}

let shutter = NSSound(
  contentsOfFile: "/System/Library/Components/CoreAudio.component/Contents/SharedSupport/SystemSounds/system/Screen Capture.aif",
  byReference: true
)

func flash(_ screen: NSScreen) {
  let window = NSWindow(contentRect: screen.frame, styleMask: .borderless, backing: .buffered, defer: false)
  window.level = .screenSaver
  window.backgroundColor = .white
  window.isOpaque = false
  window.ignoresMouseEvents = true
  window.hasShadow = false
  window.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
  window.isReleasedWhenClosed = false
  window.alphaValue = 0.85
  window.setFrame(screen.frame, display: true)
  window.orderFrontRegardless()
  NSAnimationContext.runAnimationGroup({ context in
    context.duration = 0.35
    context.timingFunction = CAMediaTimingFunction(name: .easeOut)
    window.animator().alphaValue = 0
  }, completionHandler: {
    window.orderOut(nil)
  })
}

func pressPaste() {
  let source = CGEventSource(stateID: .combinedSessionState)
  let down = CGEvent(keyboardEventSource: source, virtualKey: 9, keyDown: true)
  let up = CGEvent(keyboardEventSource: source, virtualKey: 9, keyDown: false)
  down?.flags = .maskCommand
  up?.flags = .maskCommand
  down?.post(tap: .cghidEventTap)
  up?.post(tap: .cghidEventTap)
}

func snap() {
  if busy { return }
  busy = true
  log("double-tap detected")
  let display = displayUnderMouse()
  let screen = NSScreen.screens[display - 1]
  DispatchQueue.global().async {
    let task = Process()
    task.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
    task.arguments = ["-x", "-c", "-D", String(display)]
    try? task.run()
    task.waitUntilExit()
    DispatchQueue.main.async {
      defer { busy = false }
      log("screencapture exit \(task.terminationStatus)")
      guard task.terminationStatus == 0 else { NSSound.beep(); return }
      if let shutter { shutter.play() } else { NSSound(named: "Tink")?.play() }
      flash(screen)
      guard let app = NSRunningApplication.runningApplications(withBundleIdentifier: openCodeID).first else {
        log("OpenCode not running")
        NSSound.beep()
        return
      }
      app.activate()
      log("pasting into OpenCode")
      DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { pressPaste() }
    }
  }
}

func handleFlags(_ event: NSEvent) {
  let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
  let onlyCommand = flags == .command
  if onlyCommand && !commandDown {
    commandDown = true
    otherKeyDuringPress = false
    return
  }
  if !flags.contains(.command) && commandDown {
    commandDown = false
    if otherKeyDuringPress { lastTap = 0; return }
    let now = ProcessInfo.processInfo.systemUptime
    if now - lastTap < doubleTapWindow {
      lastTap = 0
      snap()
    } else {
      lastTap = now
    }
    return
  }
  // Another modifier joined (e.g. ⌘⇧): not a plain tap.
  if commandDown && !onlyCommand { otherKeyDuringPress = true }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)

let prompt = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
_ = AXIsProcessTrustedWithOptions(prompt)
if !CGPreflightScreenCaptureAccess() { _ = CGRequestScreenCaptureAccess() }

log("started · accessibility=\(AXIsProcessTrusted()) · screen=\(CGPreflightScreenCaptureAccess()) · input=\(CGPreflightListenEventAccess())")

NSEvent.addGlobalMonitorForEvents(matching: .flagsChanged, handler: handleFlags)
NSEvent.addLocalMonitorForEvents(matching: .flagsChanged) { handleFlags($0); return $0 }
NSEvent.addGlobalMonitorForEvents(matching: [.keyDown, .leftMouseDown, .rightMouseDown]) { _ in
  if commandDown { otherKeyDuringPress = true } else { lastTap = 0 }
}

app.run()
