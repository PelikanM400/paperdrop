// paperdrop-set — the macOS half of PaperDrop.
//
// It does the two things that need AppKit:
//   1. apply one local image file to every screen, scaled to fill without
//      stretching (aspect preserved, clipped if needed);
//   2. stay resident and re-apply the cached image when the display
//      configuration changes (an external screen is plugged in or removed) or
//      when the machine wakes from sleep.
//
// Build:  ./build.sh        (produces ./paperdrop-set next to this file)
// Usage:  paperdrop-set apply <file> [--fill|--fit]
//         paperdrop-set current          -> prints the current wallpaper path
//         paperdrop-set screens          -> one line per screen
//         paperdrop-set watch <file>     -> applies, then re-applies on change
//
// IMPORTANT: as of writing this file has NOT been compiled or run on a real
// Mac. Treat everything it claims as unverified until the checklist in
// docs/ has been run on the target machine.

import AppKit
import Foundation

enum HelperError: Error, CustomStringConvertible {
    case usage
    case missingFile(String)
    case applyFailed(String)

    var description: String {
        switch self {
        case .usage:
            return """
            usage:
              paperdrop-set apply <file> [--fill|--fit]
              paperdrop-set current
              paperdrop-set screens
              paperdrop-set watch <file>
            """
        case .missingFile(let path):
            return "no such image file: \(path)"
        case .applyFailed(let detail):
            return "could not set the wallpaper: \(detail)"
        }
    }
}

/// Scale with the aspect ratio preserved: proportional scaling, and clipping
/// allowed so the image fills the screen instead of leaving empty bars.
func options(fill: Bool) -> [NSWorkspace.DesktopImageOptionKey: Any] {
    let scaling: NSImageScaling = fill
        ? .scaleProportionallyUpOrDown   // grow or shrink proportionally to fill
        : .scaleProportionallyDown       // shrink only, keep the whole image visible
    return [
        .imageScaling: scaling.rawValue,
        .allowClipping: fill,
    ]
}

func applyImage(_ path: String, fill: Bool) throws {
    let url = URL(fileURLWithPath: (path as NSString).expandingTildeInPath)
    guard FileManager.default.fileExists(atPath: url.path) else {
        throw HelperError.missingFile(path)
    }
    let screens = NSScreen.screens
    guard !screens.isEmpty else {
        throw HelperError.applyFailed("no screens reported by AppKit")
    }
    var failures: [String] = []
    for screen in screens {
        do {
            // Per-screen API: every screen gets the same file, each scaled on
            // its own. Nothing spans two screens.
            try NSWorkspace.shared.setDesktopImageURL(url, for: screen, options: options(fill: fill))
        } catch {
            failures.append(String(describing: error))
        }
    }
    if !failures.isEmpty {
        throw HelperError.applyFailed(failures.joined(separator: "; "))
    }
}

func currentWallpaperPath() -> String? {
    // desktopImageURL(for:) must be called on the main thread; a CLI already is.
    let screen = NSScreen.main ?? NSScreen.screens.first
    guard let screen = screen else { return nil }
    return NSWorkspace.shared.desktopImageURL(for: screen)?.path
}

func printScreens() {
    for (index, screen) in NSScreen.screens.enumerated() {
        let frame = screen.frame
        let name = screen.localizedName
        let scale = screen.backingScaleFactor
        let applied = NSWorkspace.shared.desktopImageURL(for: screen)?.path ?? "(unknown)"
        print("\(index)\t\(name)\t\(Int(frame.width))x\(Int(frame.height))\tscale \(scale)\t\(applied)")
    }
}

/// Applies the image now and whenever the display layout changes or the
/// machine wakes. Blocks forever; this is what the LaunchAgent keeps alive.
func watch(_ path: String, fill: Bool) throws {
    try applyImage(path, fill: fill)
    let center = NSWorkspace.shared.notificationCenter
    let reapply: (Notification) -> Void = { notification in
        do {
            try applyImage(path, fill: fill)
            FileHandle.standardError.write("paperdrop-set: re-applied after \(notification.name.rawValue)\n".data(using: .utf8)!)
        } catch {
            FileHandle.standardError.write("paperdrop-set: re-apply failed: \(error)\n".data(using: .utf8)!)
        }
    }
    center.addObserver(forName: NSApplication.didChangeScreenParametersNotification,
                       object: nil, queue: .main, using: reapply)
    center.addObserver(forName: NSWorkspace.didWakeNotification,
                       object: nil, queue: .main, using: reapply)
    RunLoop.main.run()
}

func run() throws {
    var arguments = Array(CommandLine.arguments.dropFirst())
    guard let command = arguments.first else { throw HelperError.usage }
    arguments.removeFirst()

    switch command {
    case "apply":
        guard let path = arguments.first else { throw HelperError.usage }
        let fill = !arguments.contains("--fit")   // --fill is the default
        try applyImage(path, fill: fill)
        print(path)
    case "current":
        if let path = currentWallpaperPath() {
            print(path)          // a single line: the path of the first screen
        }
    case "screens":
        printScreens()
    case "watch":
        guard let path = arguments.first else { throw HelperError.usage }
        try watch(path, fill: !arguments.contains("--fit"))
    default:
        throw HelperError.usage
    }
}

do {
    try run()
} catch let error as HelperError {
    FileHandle.standardError.write("paperdrop-set: \(error)\n".data(using: .utf8)!)
    exit(2)
} catch {
    FileHandle.standardError.write("paperdrop-set: \(error)\n".data(using: .utf8)!)
    exit(1)
}
