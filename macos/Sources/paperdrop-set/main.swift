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
//         paperdrop-set watch <file> --lock-file <path> -> legacy file argument
//         paperdrop-set watch-cache <directory> --lock-file <path> -> resolves active on each event
//
// IMPORTANT: as of writing this file has NOT been compiled or run on a real
// Mac. Treat everything it claims as unverified until the checklist in
// docs/ has been run on the target machine.

import AppKit
import Foundation
import Darwin

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
              paperdrop-set watch <file> --lock-file <path>
              paperdrop-set watch-cache <directory> --lock-file <path>
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

/// Read one atomic role index on every event. Never resolve a symlink/fixed
/// current.jpg URL and hand that alias to AppKit: image URLs must be immutable.
struct ImageIndex: Decodable {
    let version: Int
    let current: String?
    let previous: String?
    let active: String?

    enum CodingKeys: String, CodingKey { case version, current, previous, active }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        version = try container.decode(Int.self, forKey: .version)
        current = try container.decodeIfPresent(String.self, forKey: .current)
        previous = try container.decodeIfPresent(String.self, forKey: .previous)
        // Older v1 indices have no active field; explicit null means no confirmed image.
        active = container.contains(.active)
            ? try container.decodeIfPresent(String.self, forKey: .active) : current
    }
}

func checkedImagePath(_ url: URL) throws -> String {
    let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
    guard attributes[.type] as? FileAttributeType == FileAttributeType.typeRegular else {
        throw HelperError.applyFailed("cache target must be a regular non-symlink file")
    }
    return url.path
}

func cachedImagePath(_ directory: String, fallback: String? = nil) throws -> String {
    let root = URL(fileURLWithPath: (directory as NSString).expandingTildeInPath)
    let indexURL = root.appendingPathComponent("images.json")
    let indexExists = (try? FileManager.default.attributesOfItem(atPath: indexURL.path)) != nil
    if indexExists {
        _ = try checkedImagePath(indexURL)
        let index = try JSONDecoder().decode(ImageIndex.self, from: Data(contentsOf: indexURL))
        guard index.version == 1 else {
            throw HelperError.applyFailed("invalid cache image index")
        }
        for role in [index.current, index.previous, index.active] {
            if let name = role {
                guard name.range(of: "^image-[0-9a-f]{64}\\.[a-zA-Z0-9]+$",
                                 options: .regularExpression) != nil else {
                    throw HelperError.applyFailed("invalid cache image name")
                }
                _ = try checkedImagePath(root.appendingPathComponent(name))
            }
        }
        guard let name = index.active else { throw HelperError.missingFile(root.path) }
        return try checkedImagePath(root.appendingPathComponent(name))
    }
    // Compatibility with a pre-upgrade cache; Python migrates it before watch.
    let names = try FileManager.default.contentsOfDirectory(atPath: root.path)
    guard let name = names.sorted().first(where: { $0.hasPrefix("current.") }) else {
        if let fallback = fallback { return try checkedImagePath(URL(fileURLWithPath: fallback)) }
        throw HelperError.missingFile(root.path)
    }
    return try checkedImagePath(root.appendingPathComponent(name))
}

/// Applies the image now and whenever the display layout changes or the
/// machine wakes. Blocks forever; this is what the LaunchAgent keeps alive.
func withUpdateLock(_ path: String, action: () throws -> Void) throws {
    let fd = Darwin.open((path as NSString).expandingTildeInPath, O_RDWR | O_CREAT, 0o600)
    guard fd >= 0 else { throw HelperError.applyFailed("could not open update lock") }
    defer { Darwin.close(fd) }
    // Wait for an updater, then resolve anew. Never capture a URL before locking.
    // These two calls must stay UNQUALIFIED. Module Darwin exports the flock(2)
    // function *and* `struct flock`, and prefixing the call with the module name
    // resolves to the struct's initialiser instead ("argument passed to call that
    // takes no arguments"), a build failure with swiftc 6.4. Leaving the module
    // qualifier off is what makes the compiler select the syscall.
    while flock(fd, LOCK_EX) != 0 {
        if errno != EINTR { throw HelperError.applyFailed("could not acquire update lock") }
    }
    defer { _ = flock(fd, LOCK_UN) }
    try action()
}

func watch(_ resolve: @escaping () throws -> String, lockPath: String, fill: Bool) throws {
    let applyLatest = {
        try withUpdateLock(lockPath) {
            try applyImage(resolve(), fill: fill)
        }
    }
    try applyLatest()
    let center = NSWorkspace.shared.notificationCenter
    let reapply: (Notification) -> Void = { notification in
        do {
            try applyLatest()
            FileHandle.standardError.write("paperdrop-set: re-applied after \(notification.name.rawValue)\n".data(using: .utf8)!)
        } catch {
            FileHandle.standardError.write("paperdrop-set: re-apply failed: \(error)\n".data(using: .utf8)!)
        }
    }
    NotificationCenter.default.addObserver(forName: NSApplication.didChangeScreenParametersNotification,
                       object: nil, queue: .main, using: reapply)
    center.addObserver(forName: NSWorkspace.didWakeNotification,
                       object: nil, queue: .main, using: reapply)
    RunLoop.main.run()
}

func run() throws {
    var arguments = Array(CommandLine.arguments.dropFirst())
    guard let command = arguments.first else { throw HelperError.usage }
    arguments.removeFirst()

    func watchLockPath() throws -> String {
        guard let offset = arguments.firstIndex(of: "--lock-file"),
              offset + 1 < arguments.count else { throw HelperError.usage }
        return arguments[offset + 1]
    }

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
        let directory = URL(fileURLWithPath: (path as NSString).expandingTildeInPath)
            .deletingLastPathComponent().path
        try watch({ try cachedImagePath(directory, fallback: path) },
                  lockPath: try watchLockPath(), fill: !arguments.contains("--fit"))
    case "watch-cache":
        guard let directory = arguments.first else { throw HelperError.usage }
        try watch({ try cachedImagePath(directory) },
                  lockPath: try watchLockPath(), fill: !arguments.contains("--fit"))
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
