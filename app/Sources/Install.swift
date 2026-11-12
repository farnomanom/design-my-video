import AppKit
import Foundation

/// First-launch checks for an installed copy (an app with its engine inside).
extension AppModel {
    static var isAppleSilicon: Bool {
        var v: Int32 = 0
        var size = MemoryLayout<Int32>.size
        return sysctlbyname("hw.optional.arm64", &v, &size, nil, 0) == 0 && v == 1
    }

    static var bundledEngine: URL? {
        guard let res = Bundle.main.resourceURL else { return nil }
        let e = res.appendingPathComponent("engine")
        return FileManager.default.fileExists(atPath: e.appendingPathComponent("dmv-engine").path) ? e : nil
    }

    func firstLaunchChecks() {
        guard let engine = AppModel.bundledEngine else { return }
        let built = (Bundle.main.object(forInfoDictionaryKey: "DMVEngineArch") as? String) ?? ""
        if built == "arm64" && !AppModel.isAppleSilicon {
            DispatchQueue.main.async {
                let a = NSAlert()
                a.messageText = "This is the Apple Silicon version"
                a.informativeText = "This Mac has an Intel processor. Please install “Design My Video for Intel Macs” instead."
                a.runModal()
            }
            return
        }
        // Downloaded files carry Apple's quarantine flag; clear it on our own engine so its helpers can run.
        let x = Process()
        x.executableURL = URL(fileURLWithPath: "/usr/bin/xattr")
        x.arguments = ["-dr", "com.apple.quarantine", engine.path]
        try? x.run()
        x.waitUntilExit()
        let version = (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "?"
        if UserDefaults.standard.string(forKey: "selftestPassed") != version {
            runSelfTest(quietIfOK: true)
        }
    }

    /// Runs `engine selftest`; shows the result (only failures when quietIfOK).
    func runSelfTest(quietIfOK: Bool = false) {
        var result: [String: Any]?
        _ = Engine.run(["selftest"], onEvent: { ev in
            if (ev["event"] as? String) == "selftest" { result = ev }
        }, completion: { code, err in
            let version = (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "?"
            let ok = (result?["ok"] as? Bool) ?? false
            if ok { UserDefaults.standard.set(version, forKey: "selftestPassed") }
            if ok && quietIfOK { return }
            let checks = (result?["checks"] as? [[String: Any]]) ?? []
            let lines = checks.map { c -> String in
                let mark = (c["ok"] as? Bool) == true ? "✓" : ((c["required"] as? Bool) == true ? "✗" : "–")
                return "\(mark) \(c["name"] as? String ?? "")"
            }
            let f = result?["features"] as? [String: Any] ?? [:]
            let chip = f["chip"] as? String ?? (AppModel.isAppleSilicon ? "Apple Silicon" : "Intel")
            let a = NSAlert()
            a.messageText = ok ? "Everything works on this Mac (\(chip))" : "Some parts of Design My Video aren't working"
            a.informativeText = (lines.isEmpty ? String(err.suffix(400)) : lines.joined(separator: "\n"))
                + (ok ? "" : "\n\nTry reinstalling from the installer. If it keeps failing, send this list to whoever gave you the app.")
            a.alertStyle = ok ? .informational : .warning
            a.runModal()
        })
    }
}
