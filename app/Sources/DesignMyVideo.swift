import AppKit
import SwiftUI
import UniformTypeIdentifiers
import WebKit

// MARK: - App

@main
struct DesignMyVideoApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup("Design My Video") {
            ContentView()
                .environmentObject(model)
                .frame(minWidth: 1000, minHeight: 700)
                .onAppear { model.start() }
        }
        .commands {
            CommandGroup(replacing: .newItem) {}
            CommandGroup(replacing: .help) {
                Button("Design My Video User Manual") { model.showManual = true }
                    .keyboardShortcut("?", modifiers: .command)
                Button("Run Self-Test") { model.runSelfTest() }
            }
        }

        Settings {
            SettingsView().environmentObject(model)
        }
    }
}

// MARK: - Data

struct CategoryScore: Codable, Identifiable {
    var id: String { category }
    let category: String
    let score: Int
    let max: Int
    let rationale: String
}

struct Suggestion: Codable, Identifiable {
    let id: String
    let startSeconds: Double
    let endSeconds: Double
    let category: String
    let severity: String
    let issue: String
    let suggestion: String
    let howInFinalCut: String
    let action: String
    let expectedGain: Int
}

struct Review: Codable {
    let overallScore: Int
    let verdict: String
    let toneAndMood: String
    let strengths: [String]
    let categories: [CategoryScore]
    let suggestions: [Suggestion]
    let providerLabel: String?
}

struct Shot: Codable {
    let index: Int
    let start: Double
    let end: Double
    let frame: String?
}

struct AnalysisFile: Codable {
    let name: String
    let shots: [Shot]?
}

struct RunSummary: Identifiable, Hashable {
    var id: URL { dir }
    let dir: URL
    let name: String
    let date: Date
    let score: Int?
}

struct ProviderInfo: Identifiable {
    let id: String
    let label: String
    let available: Bool
    let defaultModel: String?
}

struct PlanOp: Identifiable {
    let id: Int
    let op: String
    let note: String
    let suggestionId: String?
    let ready: Bool
    let detail: String
}

struct EditPlanFile {
    let summary: String
    let ops: [PlanOp]
    let notPossible: [String]
    let command: String?
    let providerLabel: String?
    let base: String

    init?(url: URL) {
        guard let d = try? Data(contentsOf: url),
              let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { return nil }
        summary = o["summary"] as? String ?? ""
        notPossible = o["not_possible"] as? [String] ?? []
        command = o["command"] as? String
        providerLabel = o["providerLabel"] as? String
        base = o["base"] as? String ?? ""
        ops = (o["ops"] as? [[String: Any]] ?? []).enumerated().map { i, op in
            PlanOp(id: i, op: op["op"] as? String ?? "", note: op["note"] as? String ?? "",
                   suggestionId: op["suggestion_id"] as? String, ready: (op["check"] as? String) == "applied",
                   detail: op["detail"] as? String ?? "")
        }
    }
}

struct VersionInfo: Identifiable {
    var id: Int { version }
    let version: Int
    let path: URL
    let created: String
    let summary: String
    let applied: Int
    let skipped: Int
    let overwrote: Bool
    let seconds: Double?
}

let opLabels: [String: String] = [
    "set_volume": "Volume", "duck": "Duck music", "fade_audio": "Audio fade", "add_transition": "Add transition",
    "remove_transition": "Remove transition", "add_title": "Add title", "restyle_title": "Restyle title",
    "copy_grade": "Match colour", "trim": "Trim", "duplicate": "Add overlay", "set_enabled": "Show / hide",
    "add_marker": "Marker", "cut_range": "Cut section", "move_range": "Move section", "swap_take": "Swap take",
]

let providerOrder = ["claude_code", "anthropic", "openai", "gemini", "ollama"]
let keyedProviders: Set<String> = ["anthropic", "openai", "gemini"]

func duration(_ s: Double) -> String {
    if s < 1 { return "under 1s" }
    let t = Int(s.rounded())
    if t < 60 { return "\(t)s" }
    if t < 3600 { return "\(t / 60)m \(String(format: "%02d", t % 60))s" }
    return "\(t / 3600)h \(String(format: "%02d", t / 60 % 60))m"
}

func timecode(_ s: Double, fps: Double = 25) -> String {
    let total = Int((max(0, s) * fps).rounded())
    let f = Int(fps.rounded())
    let sec = total / f
    return String(format: "%02d:%02d:%02d:%02d", sec / 3600, sec / 60 % 60, sec % 60, total % f)
}

// MARK: - Engine

enum Engine {
    static var outputDir: URL {
        if let custom = UserDefaults.standard.string(forKey: "outputDir"), !custom.isEmpty {
            return URL(fileURLWithPath: custom)
        }
        let movies = FileManager.default.urls(for: .moviesDirectory, in: .userDomainMask).first!
        return movies.appendingPathComponent("Design My Video")
    }

    /// Bundled engine inside the app, or the development project folder named in Info.plist.
    static func command(_ args: [String]) -> (URL, [String], URL?) {
        if let res = Bundle.main.resourceURL {
            let bin = res.appendingPathComponent("engine/dmv-engine")
            if FileManager.default.isExecutableFile(atPath: bin.path) {
                return (bin, args, res.appendingPathComponent("engine"))
            }
        }
        let dev = (Bundle.main.object(forInfoDictionaryKey: "DMVDevProjectPath") as? String) ?? ""
        return (URL(fileURLWithPath: dev + "/.venv/bin/python"), [dev + "/engine.py"] + args, nil)
    }

    static func environment(extraPath: URL?) -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        var paths = ["/usr/local/bin", "/opt/homebrew/bin", "\(home)/.local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
        if let extra = extraPath { paths.insert(extra.path, at: 0) }
        env["PATH"] = paths.joined(separator: ":")
        env["DMV_PROGRESS_STDOUT"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        env["DMV_OUTPUT_DIR"] = outputDir.path
        return env
    }

    @discardableResult
    static func run(_ args: [String], stdin: String? = nil,
                    onEvent: @escaping ([String: Any]) -> Void,
                    completion: @escaping (Int32, String) -> Void) -> Process {
        let (exe, fullArgs, extra) = command(args)
        let p = Process()
        p.executableURL = exe
        p.arguments = fullArgs
        p.environment = environment(extraPath: extra)
        let out = Pipe(), err = Pipe()
        p.standardOutput = out
        p.standardError = err
        var buffer = Data()
        var errText = ""
        let lock = NSLock()
        out.fileHandleForReading.readabilityHandler = { h in
            let chunk = h.availableData
            guard !chunk.isEmpty else { return }
            lock.lock()
            buffer.append(chunk)
            while let nl = buffer.firstIndex(of: 0x0A) {
                let line = buffer.subdata(in: buffer.startIndex..<nl)
                buffer.removeSubrange(buffer.startIndex...nl)
                if let obj = try? JSONSerialization.jsonObject(with: line) as? [String: Any] {
                    DispatchQueue.main.async { onEvent(obj) }
                }
            }
            lock.unlock()
        }
        err.fileHandleForReading.readabilityHandler = { h in
            let chunk = h.availableData
            if let s = String(data: chunk, encoding: .utf8) {
                lock.lock(); errText += s; if errText.count > 20000 { errText = String(errText.suffix(20000)) }; lock.unlock()
            }
        }
        p.terminationHandler = { proc in
            out.fileHandleForReading.readabilityHandler = nil
            err.fileHandleForReading.readabilityHandler = nil
            lock.lock(); let e = errText; lock.unlock()
            DispatchQueue.main.async { completion(proc.terminationStatus, e) }
        }
        if let stdin {
            let inPipe = Pipe()
            p.standardInput = inPipe
            try? p.run()
            inPipe.fileHandleForWriting.write((stdin + "\n").data(using: .utf8)!)
            try? inPipe.fileHandleForWriting.close()
        } else {
            do { try p.run() } catch {
                DispatchQueue.main.async { completion(-1, "Could not start the engine: \(error.localizedDescription)") }
            }
        }
        return p
    }
}

// MARK: - Model

final class AppModel: ObservableObject {
    /// Which main screen is showing (home, a new edit analysis, new clips, an analysis, a clip project).
    @Published var screen: Screen = .home
    @Published var clipProjects: [ClipProject] = []
    @Published var currentClips: ClipProject?
    var pendingClipDir: URL?
    var didFirstLaunchChecks = false
    var revealAfter: URL?
    /// The user manual sheet. Opens at every launch unless the user turned it off (default: on).
    @Published var showManual = false
    private var offeredManual = false
    @Published var videoURL: URL?
    @Published var fcpxmlURL: URL?
    @Published var endText = ""
    @Published var whisper = "small"
    @Published var running = false
    @Published var percent: Double = 0
    @Published var note = ""
    @Published var stage = ""
    @Published var startedAt: Date?
    @Published var errorMessage: String?
    @Published var runs: [RunSummary] = []
    @Published var selected: RunSummary?
    @Published var review: Review?
    @Published var shots: [Shot] = []
    @Published var approved: Set<String> = []
    @Published var providers: [ProviderInfo] = []
    @Published var markersPath: URL?
    @Published var busyMessage: String?
    @Published var plan: EditPlanFile?
    @Published var planChosen: Set<Int> = []
    @Published var showPlan = false
    @Published var showRender = false
    @Published var lastRender: URL?
    @Published var versions: [VersionInfo] = []
    @Published var commandText = ""
    @Published var fromOriginal = false
    @Published var justCreated: VersionInfo?
    @Published var overwriteOriginal = false
    @Published var overwriteStep = 0
    @Published var timings: [String: Double] = [:]
    @Published var pendingDeleteRun: RunSummary?
    @Published var pendingDeleteVersion: VersionInfo?
    @Published var confirmDeleteAll = false
    var process: Process?

    var provider: String {
        get { UserDefaults.standard.string(forKey: "provider") ?? "auto" }
        set { UserDefaults.standard.set(newValue, forKey: "provider"); objectWillChange.send() }
    }

    func model(for provider: String) -> String? {
        let v = UserDefaults.standard.string(forKey: "model.\(provider)") ?? ""
        return v.isEmpty ? nil : v
    }

    func start() {
        try? FileManager.default.createDirectory(at: Engine.outputDir, withIntermediateDirectories: true)
        loadRuns()
        loadClipProjects()
        refreshProviders()
        offerManualOnLaunch()
        if !didFirstLaunchChecks { didFirstLaunchChecks = true; firstLaunchChecks() }
        selfCheckIfRequested()
    }

    private func offerManualOnLaunch() {
        guard !offeredManual else { return }
        offeredManual = true
        let args = CommandLine.arguments
        if args.contains("--snapshot") && !args.contains("--manual") { return }  // keep test snapshots unobstructed
        let wanted = UserDefaults.standard.object(forKey: ManualView.launchKey) as? Bool ?? true
        if wanted { DispatchQueue.main.async { self.showManual = true } }
    }

    /// Test hook: `--snapshot <folder> [--open-first]` renders each window to PNG, then quits.
    private func selfCheckIfRequested() {
        let args = CommandLine.arguments
        guard let i = args.firstIndex(of: "--snapshot"), i + 1 < args.count else { return }
        let folder = URL(fileURLWithPath: args[i + 1])
        if args.contains("--open-first") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                self.select(self.runs.first)
                if args.contains("--show-plan") { self.loadPlan(); self.showPlan = self.plan != nil }
            }
        }
        if args.contains("--settings") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 560, height: 640),
                                 styleMask: [.titled, .closable], backing: .buffered, defer: false)
                w.title = "Settings"
                w.contentView = NSHostingView(rootView: SettingsView().environmentObject(self))
                w.isReleasedWhenClosed = false
                w.makeKeyAndOrderFront(nil)
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 4) {
            try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            for (n, w) in NSApp.windows.enumerated() where w.isVisible {
                guard let view = w.contentView?.superview ?? w.contentView,
                      let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { continue }
                view.cacheDisplay(in: view.bounds, to: rep)
                try? rep.representation(using: .png, properties: [:])?.write(to: folder.appendingPathComponent("window_\(n).png"))
            }
            exit(0)  // terminate() can stall while a sheet is open
        }
    }

    func refreshProviders() {
        Engine.run(["providers"], onEvent: { [weak self] ev in
            guard ev["event"] as? String == "providers", let p = ev["providers"] as? [String: [String: Any]] else { return }
            self?.providers = providerOrder.compactMap { key in
                guard let v = p[key] else { return nil }
                return ProviderInfo(id: key, label: v["label"] as? String ?? key,
                                    available: v["available"] as? Bool ?? false,
                                    defaultModel: v["defaultModel"] as? String)
            }
        }, completion: { _, _ in })
    }

    func loadRuns() {
        let fm = FileManager.default
        let dirs = (try? fm.contentsOfDirectory(at: Engine.outputDir, includingPropertiesForKeys: [.contentModificationDateKey])) ?? []
        runs = dirs.compactMap { d in
            guard fm.fileExists(atPath: d.appendingPathComponent("analysis.json").path) else { return nil }
            let date = (try? d.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            var score: Int?
            if let data = try? Data(contentsOf: d.appendingPathComponent("review.json")),
               let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                score = obj["overall_score"] as? Int
            }
            return RunSummary(dir: d, name: d.lastPathComponent, date: date, score: score)
        }.sorted { $0.date > $1.date }
    }

    func select(_ run: RunSummary?) {
        selected = run
        if run != nil { screen = .run; currentClips = nil }
        review = nil; shots = []; approved = []; markersPath = nil
        plan = nil; showPlan = false; versions = []; justCreated = nil; lastRender = nil
        guard let run else { timings = [:]; return }
        loadVersions()
        loadTimings()
        let dec = JSONDecoder()
        dec.keyDecodingStrategy = .convertFromSnakeCase
        if let d = try? Data(contentsOf: run.dir.appendingPathComponent("review.json")) {
            review = try? dec.decode(Review.self, from: d)
        }
        if let d = try? Data(contentsOf: run.dir.appendingPathComponent("analysis.json")),
           let a = try? JSONDecoder().decode(AnalysisFile.self, from: d) {
            shots = a.shots ?? []
        }
        if let d = try? Data(contentsOf: run.dir.appendingPathComponent("approvals.json")),
           let ids = try? JSONDecoder().decode([String].self, from: d) {
            approved = Set(ids)
        }
        let m = run.dir.appendingPathComponent("\(run.name) – Design review markers.fcpxml")
        if FileManager.default.fileExists(atPath: m.path) { markersPath = m }
    }

    func toggle(_ id: String) {
        if approved.contains(id) { approved.remove(id) } else { approved.insert(id) }
        guard let run = selected, let data = try? JSONEncoder().encode(Array(approved).sorted()) else { return }
        try? data.write(to: run.dir.appendingPathComponent("approvals.json"))
    }

    func frameURL(at t: Double) -> URL? {
        guard let run = selected, let s = shots.first(where: { $0.start <= t && t < $0.end }), let f = s.frame else { return nil }
        return run.dir.appendingPathComponent(f)
    }

    func parseEnd() -> Double? {
        let t = endText.trimmingCharacters(in: .whitespaces)
        if t.isEmpty { return nil }
        let parts = t.split(separator: ":").compactMap { Double($0) }
        return parts.reduce(0) { $0 * 60 + $1 }
    }

    func analyse() {
        guard videoURL != nil || fcpxmlURL != nil else { return }
        var args = ["all"]
        if let v = videoURL { args += ["--video", v.path] }
        if let f = fcpxmlURL { args += ["--fcpxml", f.path] }
        if let e = parseEnd(), e > 0 { args += ["--end", String(e)] }
        args += ["--whisper", whisper, "--provider", provider]
        if let m = model(for: provider) { args += ["--model", m] }
        let base = (videoURL ?? fcpxmlURL)!.deletingPathExtension().lastPathComponent
        let stamp = DateFormatter.localizedString(from: Date(), dateStyle: .short, timeStyle: .short)
            .replacingOccurrences(of: "/", with: "-").replacingOccurrences(of: ":", with: ".")
        args += ["--name", "\(base) (\(stamp))"]
        launch(args, title: "Analysing")
    }

    func rerunReview(provider: String) {
        guard let run = selected else { return }
        var args = ["review", run.dir.path, "--provider", provider]
        if let m = model(for: provider) { args += ["--model", m] }
        launch(args, title: "Reviewing") { [weak self] in
            Engine.run(["report", run.dir.path], onEvent: { _ in }, completion: { _, _ in
                self?.loadRuns(); self?.select(self?.runs.first { $0.dir == run.dir })
            })
        }
    }

    private func launch(_ args: [String], title: String, then: (() -> Void)? = nil) {
        running = true; percent = 0; note = "Starting…"; stage = title; errorMessage = nil; startedAt = Date()
        var lastDir: URL?
        process = Engine.run(args, onEvent: { [weak self] ev in
            guard let self else { return }
            switch ev["event"] as? String {
            case "progress":
                if let label = ev["label"] as? String { self.stage = label }
                if let p = ev["percent"] as? Double { self.percent = p } else if let p = ev["percent"] as? Int { self.percent = Double(p) }
                if let n = ev["note"] as? String { self.note = n }
            case "analysis":
                if let d = ev["dir"] as? String { lastDir = URL(fileURLWithPath: d) }
            case "error":
                self.errorMessage = ev["message"] as? String
            default: break
            }
        }, completion: { [weak self] code, err in
            guard let self else { return }
            self.running = false
            self.process = nil
            if code != 0 && self.errorMessage == nil {
                let lines = err.split(separator: "\n").suffix(6).joined(separator: "\n")
                self.errorMessage = code == 15 ? "Stopped." : (lines.isEmpty ? "The engine stopped (code \(code))." : lines)
            }
            self.loadRuns()
            if let d = lastDir, let r = self.runs.first(where: { $0.dir.standardizedFileURL == d.standardizedFileURL }) {
                self.select(r)
            }
            if code == 0 { then?() }
        })
    }

    func cancel() { process?.terminate() }

    func exportMarkers() {
        guard let run = selected, let f = fcpxmlSource(for: run) else {
            errorMessage = "The original FCPXML for this analysis wasn't found."
            return
        }
        busyMessage = "Writing markers…"
        var args = ["markers", run.dir.path, "--fcpxml", f]
        if !approved.isEmpty { args += ["--ids", approved.sorted().joined(separator: ",")] }
        Engine.run(args, onEvent: { [weak self] ev in
            if ev["event"] as? String == "markers", let p = ev["path"] as? String {
                self?.markersPath = URL(fileURLWithPath: p)
            }
            if ev["event"] as? String == "error" { self?.errorMessage = ev["message"] as? String }
        }, completion: { [weak self] code, err in
            self?.busyMessage = nil
            if code != 0 { self?.errorMessage = "Marker export failed. \(err.suffix(400))" }
        })
    }

    // MARK: Edits

    // MARK: Deleting (everything goes to the Trash, so it can be put back from there)

    func trash(_ run: RunSummary) {
        guard !running else { errorMessage = "Wait for the current job to finish, or stop it, before deleting."; return }
        do { try FileManager.default.trashItem(at: run.dir, resultingItemURL: nil) }
        catch { errorMessage = "Couldn't move “\(run.name)” to the Trash: \(error.localizedDescription)"; return }
        if selected?.dir == run.dir { select(nil) }
        loadRuns()
    }

    func trashAllRuns() {
        guard !running else { errorMessage = "Wait for the current job to finish, or stop it, before deleting."; return }
        var failed: [String] = []
        for r in runs {
            do { try FileManager.default.trashItem(at: r.dir, resultingItemURL: nil) } catch { failed.append(r.name) }
        }
        select(nil)
        loadRuns()
        if !failed.isEmpty { errorMessage = "Couldn't move these to the Trash: " + failed.joined(separator: ", ") }
    }

    /// Removes an edited version made by the app. For an overwrite entry only the log goes: the file at that path is the
    /// person's own timeline, which the app never deletes.
    func trash(_ v: VersionInfo) {
        guard let run = selected else { return }
        guard !running else { errorMessage = "Wait for the current job to finish, or stop it, before deleting."; return }
        let fm = FileManager.default
        let log = run.dir.appendingPathComponent("versions").appendingPathComponent("v\(v.version).json")
        do {
            if !v.overwrote && fm.fileExists(atPath: v.path.path) { try fm.trashItem(at: v.path, resultingItemURL: nil) }
            if fm.fileExists(atPath: log.path) { try fm.trashItem(at: log, resultingItemURL: nil) }
        } catch {
            errorMessage = "Couldn't move version \(v.version) to the Trash: \(error.localizedDescription)"
        }
        if justCreated?.version == v.version { justCreated = nil }
        loadVersions()
    }

    func loadVersions() {
        guard let run = selected else { versions = []; return }
        let dir = run.dir.appendingPathComponent("versions")
        let logs = (try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)) ?? []
        versions = logs.filter { $0.pathExtension == "json" && $0.lastPathComponent.hasPrefix("v") }.compactMap { u in
            guard let d = try? Data(contentsOf: u), let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
                  let n = o["version"] as? Int, let p = o["path"] as? String,
                  FileManager.default.fileExists(atPath: p) else { return nil }
            return VersionInfo(version: n, path: URL(fileURLWithPath: p), created: o["created"] as? String ?? "",
                               summary: o["summary"] as? String ?? "", applied: o["applied"] as? Int ?? 0,
                               skipped: o["skipped"] as? Int ?? 0, overwrote: o["overwroteOriginal"] as? Bool ?? false,
                               seconds: o["seconds"] as? Double)
        }.sorted { $0.version > $1.version }
    }

    func loadPlan() {
        guard let run = selected else { return }
        plan = EditPlanFile(url: run.dir.appendingPathComponent("plan.json"))
        planChosen = Set(plan?.ops.filter(\.ready).map(\.id) ?? [])
        overwriteOriginal = false; overwriteStep = 0
    }

    /// Ask the AI to turn approved suggestions (or a typed instruction) into concrete edits, then show them for approval.
    func planEdits(command: String? = nil) {
        guard let run = selected else { return }
        guard fcpxmlSource(for: run) != nil else {
            errorMessage = "The original FCPXML for this analysis wasn't found, so there is no timeline to edit."
            return
        }
        var args = ["plan", run.dir.path, "--base", fromOriginal ? "original" : "latest", "--provider", provider]
        if let c = command?.trimmingCharacters(in: .whitespacesAndNewlines), !c.isEmpty {
            args += ["--command", c]
        } else if !approved.isEmpty {
            args += ["--ids", approved.sorted().joined(separator: ",")]
        } else { return }
        if let m = model(for: provider) { args += ["--model", m] }
        let keep = approved
        launch(args, title: "Planning edits") { [weak self] in
            guard let self else { return }
            self.select(self.runs.first { $0.dir == run.dir })
            self.approved = keep
            self.loadPlan()
            self.showPlan = self.plan != nil
        }
    }

    /// Word-for-word transcript of the dialogue, then one planned cut per um/uh/er (reviewed in the plan sheet).
    func findFillers() {
        guard let run = selected else { return }
        guard fcpxmlSource(for: run) != nil else {
            errorMessage = "The original FCPXML for this analysis wasn't found, so there is no timeline to edit."
            return
        }
        let keep = approved
        launch(["fillers", run.dir.path, "--base", fromOriginal ? "original" : "latest"], title: "Finding filler words") { [weak self] in
            guard let self else { return }
            self.select(self.runs.first { $0.dir == run.dir })
            self.approved = keep
            self.loadPlan()
            self.showPlan = self.plan != nil
        }
    }

    /// Render a version of the timeline to a video file.
    func render(base: String, preset: String, loudness: String) {
        guard let run = selected else { return }
        lastRender = nil
        var out: URL?
        let args = ["render", run.dir.path, "--base", base, "--preset", preset, "--loudness", loudness]
        running = true; percent = 0; note = "Starting…"; stage = "Rendering"; errorMessage = nil; startedAt = Date()
        process = Engine.run(args, onEvent: { [weak self] ev in
            guard let self else { return }
            switch ev["event"] as? String {
            case "progress":
                if let l = ev["label"] as? String { self.stage = l }
                if let p = ev["percent"] as? Double { self.percent = p } else if let p = ev["percent"] as? Int { self.percent = Double(p) }
                if let n = ev["note"] as? String { self.note = n }
            case "render":
                if let pth = ev["path"] as? String { out = URL(fileURLWithPath: pth) }
            case "error":
                self.errorMessage = ev["message"] as? String
            default: break
            }
        }, completion: { [weak self] code, err in
            guard let self else { return }
            self.running = false
            self.process = nil
            if code != 0 && self.errorMessage == nil {
                self.errorMessage = code == 15 ? "Stopped." : "The render stopped (code \(code))."
            }
            self.lastRender = out
            self.loadTimings()
        })
    }

    func loadTimings() {
        timings = [:]
        guard let run = selected, let d = try? Data(contentsOf: run.dir.appendingPathComponent("timings.json")),
              let o = try? JSONSerialization.jsonObject(with: d) as? [String: [String: Any]] else { return }
        for (k, v) in o { if let s = v["seconds"] as? Double { timings[k] = s } else if let s = v["seconds"] as? Int { timings[k] = Double(s) } }
    }

    /// New project by default. `overwrite` is only true after both confirmation steps in the plan sheet.
    func applyPlan(overwrite: Bool = false) {
        guard let run = selected, !planChosen.isEmpty else { return }
        showPlan = false
        overwriteStep = 0
        busyMessage = overwrite ? "Overwriting the original timeline…" : "Applying edits to a new project…"
        var args = ["apply", run.dir.path, "--ops", planChosen.sorted().map(String.init).joined(separator: ",")]
        if overwrite { args += ["--overwrite", "CONFIRMED"] }
        overwriteOriginal = false
        Engine.run(args, onEvent: { [weak self] ev in
            if ev["event"] as? String == "error" { self?.errorMessage = ev["message"] as? String }
        }, completion: { [weak self] code, err in
            guard let self else { return }
            self.busyMessage = nil
            if code != 0 && self.errorMessage == nil { self.errorMessage = "Applying the edits failed. \(err.suffix(400))" }
            self.loadVersions()
            self.loadTimings()
            if code == 0 { self.justCreated = self.versions.first; self.commandText = "" }
        })
    }

    func fcpxmlSource(for run: RunSummary) -> String? {
        guard let d = try? Data(contentsOf: run.dir.appendingPathComponent("analysis.json")),
              let obj = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
              let tl = obj["timeline"] as? [String: Any], let f = tl["file"] as? String else { return nil }
        return FileManager.default.fileExists(atPath: f) ? f : nil
    }

    func saveKey(_ provider: String, _ key: String, done: @escaping (Bool) -> Void) {
        Engine.run(["set-key", provider], stdin: key, onEvent: { _ in }, completion: { [weak self] code, _ in
            done(code == 0); self?.refreshProviders()
        })
    }

    func deleteKey(_ provider: String) {
        Engine.run(["delete-key", provider], onEvent: { _ in }, completion: { [weak self] _, _ in self?.refreshProviders() })
    }
}

// MARK: - Views

let accent = Color(red: 0.92, green: 0.45, blue: 0.18)

struct ContentView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        NavigationSplitView {
            List(selection: Binding(get: { model.selected }, set: { model.select($0) })) {
                Section {
                    Button { model.select(nil); model.currentClips = nil; model.screen = .home } label: { Label("Home", systemImage: "house") }
                        .buttonStyle(.plain)
                    Button { model.select(nil); model.currentClips = nil; model.screen = .newEdit } label: { Label("New analysis", systemImage: "plus.circle.fill") }
                        .buttonStyle(.plain).foregroundStyle(accent)
                    Button { model.select(nil); model.currentClips = nil; model.screen = .newClips } label: { Label("New clips", systemImage: "scissors") }
                        .buttonStyle(.plain).foregroundStyle(accent)
                }
                Section("Clips") {
                    if model.clipProjects.isEmpty { Text("None yet").foregroundStyle(.secondary) }
                    ForEach(model.clipProjects) { p in
                        Button { model.openClips(p) } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(p.name).lineLimit(1)
                                    Text("\(p.clips.count) clips · \(p.date.formatted(date: .abbreviated, time: .omitted))").font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                            }
                            .padding(.vertical, 2).padding(.horizontal, 4)
                            .background(model.currentClips?.dir == p.dir && model.screen == .clips ? accent.opacity(0.18) : Color.clear,
                                        in: RoundedRectangle(cornerRadius: 6))
                            .contentShape(Rectangle())
                        }.buttonStyle(.plain)
                        .contextMenu {
                            Button { NSWorkspace.shared.activateFileViewerSelecting([p.dir]) } label: { Label("Show in Finder", systemImage: "folder") }
                            Divider()
                            Button(role: .destructive) { model.trashClips(p) } label: { Label("Move to Trash", systemImage: "trash") }
                        }
                    }
                }
                Section("Edit & design") {
                    if model.runs.isEmpty { Text("None yet").foregroundStyle(.secondary) }
                    ForEach(model.runs) { run in
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(run.name).lineLimit(1)
                                Text(run.date, style: .date).font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            if let s = run.score {
                                Text("\(s)").font(.callout.monospacedDigit().bold())
                                    .padding(.horizontal, 7).padding(.vertical, 2)
                                    .background(accent.opacity(0.18), in: Capsule())
                            }
                        }.tag(run)
                        .contextMenu {
                            Button { NSWorkspace.shared.activateFileViewerSelecting([run.dir]) } label: { Label("Show in Finder", systemImage: "folder") }
                            Divider()
                            Button(role: .destructive) { model.pendingDeleteRun = run } label: { Label("Move to Trash…", systemImage: "trash") }
                        }
                    }
                }
            }
            .navigationSplitViewColumnWidth(min: 230, ideal: 270)
        } detail: {
            Group {
                if model.running { ProgressPanel() }
                else {
                    switch model.screen {
                    case .newEdit: NewAnalysisView()
                    case .newClips: NewClipsView()
                    case .run: if model.selected != nil { ResultsView() } else { HomeView() }
                    case .clips: if let p = model.currentClips { ClipsView(project: p) } else { HomeView() }
                    case .home: HomeView()
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .sheet(isPresented: $model.showManual) { ManualView() }
        .alert("Something went wrong", isPresented: Binding(get: { model.errorMessage != nil && !model.running },
                                                           set: { if !$0 { model.errorMessage = nil } })) {
            Button("OK", role: .cancel) {}
        } message: { Text(model.errorMessage ?? "") }
        .alert("Move “\(model.pendingDeleteRun?.name ?? "")” to the Trash?",
               isPresented: Binding(get: { model.pendingDeleteRun != nil }, set: { if !$0 { model.pendingDeleteRun = nil } })) {
            Button("Move to Trash", role: .destructive) { if let r = model.pendingDeleteRun { model.trash(r) }; model.pendingDeleteRun = nil }
            Button("Cancel", role: .cancel) { model.pendingDeleteRun = nil }
        } message: {
            Text("This removes the analysis, its review and every edited version the app made from it. Your video and your original Final Cut timeline are not touched. You can put it back from the Trash until you empty it.")
        }
    }
}

struct DropZone: View {
    let title: String
    let subtitle: String
    let icon: String
    let types: [UTType]
    @Binding var url: URL?
    @State private var hovering = false

    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: url == nil ? icon : "checkmark.circle.fill")
                .font(.system(size: 34)).foregroundStyle(url == nil ? Color.secondary : accent)
            Text(url?.lastPathComponent ?? title).font(.headline).lineLimit(2).multilineTextAlignment(.center)
            Text(url == nil ? subtitle : "Click to change").font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, minHeight: 170)
        .padding()
        .background(RoundedRectangle(cornerRadius: 14).fill(hovering ? accent.opacity(0.08) : Color.primary.opacity(0.03)))
        .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [6, 5]))
            .foregroundStyle(hovering ? accent : Color.secondary.opacity(0.5)))
        .contentShape(Rectangle())
        .onTapGesture { choose() }
        .onDrop(of: [.fileURL], isTargeted: $hovering) { items in
            guard let item = items.first else { return false }
            _ = item.loadObject(ofClass: URL.self) { u, _ in
                if let u { DispatchQueue.main.async { url = u } }
            }
            return true
        }
    }

    func choose() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = types
        panel.canChooseDirectories = types.contains { $0.identifier.contains("fcpxml") }
        panel.treatsFilePackagesAsDirectories = false
        if panel.runModal() == .OK { url = panel.url }
    }
}

struct NewAnalysisView: View {
    @EnvironmentObject var model: AppModel

    var fcpTypes: [UTType] {
        [UTType(filenameExtension: "fcpxml"), UTType(filenameExtension: "fcpxmld"), .xml].compactMap { $0 }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Design My Video").font(.system(size: 30, weight: .bold))
                    Text("Drop in your finished edit. The app watches and listens to it, scores it out of 100 and suggests design improvements. Approve the ones you like and it makes the edits for you in a new copy of your Final Cut timeline.")
                        .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                HStack(spacing: 16) {
                    DropZone(title: "Rendered video", subtitle: "MP4 or MOV · picture and sound", icon: "film",
                             types: [.movie, .mpeg4Movie, .quickTimeMovie], url: $model.videoURL)
                    DropZone(title: "Final Cut timeline", subtitle: "FCPXML · File → Export XML", icon: "list.bullet.rectangle",
                             types: fcpTypes, url: $model.fcpxmlURL)
                }
                GroupBox {
                    Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 12) {
                        GridRow {
                            Text("AI reviewer")
                            Picker("", selection: Binding(get: { model.provider }, set: { model.provider = $0 })) {
                                Text("Automatic (Claude first)").tag("auto")
                                ForEach(model.providers) { p in
                                    Text(p.label + (p.available ? "" : " — not set up")).tag(p.id)
                                }
                            }.labelsHidden().frame(maxWidth: 320)
                        }
                        GridRow {
                            Text("Transcript quality")
                            Picker("", selection: $model.whisper) {
                                Text("Fast").tag("base"); Text("Balanced").tag("small"); Text("Best (slow)").tag("medium")
                            }.pickerStyle(.segmented).labelsHidden().frame(maxWidth: 320)
                        }
                        GridRow {
                            Text("Analyse up to")
                            HStack {
                                TextField("whole film", text: $model.endText).frame(width: 110)
                                Text("mm:ss — optional, e.g. 10:03").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }.padding(6)
                }
                HStack {
                    Spacer()
                    Button { model.analyse() } label: {
                        Label("Analyse film", systemImage: "sparkles").padding(.horizontal, 14).padding(.vertical, 6)
                    }
                    .buttonStyle(.borderedProminent).tint(accent).controlSize(.large)
                    .disabled(model.videoURL == nil && model.fcpxmlURL == nil)
                }
            }
            .padding(28)
            .frame(maxWidth: 860)
            .frame(maxWidth: .infinity)
        }
    }
}

struct ProgressPanel: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        VStack(spacing: 18) {
            Text(model.stage).font(.title2.bold()).multilineTextAlignment(.center)
            ProgressView(value: model.percent, total: 100).tint(accent).frame(maxWidth: 480)
            HStack {
                Text("\(Int(model.percent))%").font(.title3.monospacedDigit().bold())
                if let s = model.startedAt {
                    TimelineView(.periodic(from: .now, by: 1)) { ctx in
                        let e = Int(ctx.date.timeIntervalSince(s))
                        Text(String(format: "· %d:%02d elapsed", e / 60, e % 60)).foregroundStyle(.secondary).monospacedDigit()
                    }
                }
            }
            Text(model.note).foregroundStyle(.secondary).multilineTextAlignment(.center)
            Text("You can keep working; the analysis carries on in the background.").font(.caption).foregroundStyle(.tertiary)
            Button("Stop", role: .destructive) { model.cancel() }
        }
        .padding(40)
    }
}

struct ScoreRing: View {
    let score: Int
    var body: some View {
        ZStack {
            Circle().stroke(Color.primary.opacity(0.1), lineWidth: 12)
            Circle().trim(from: 0, to: CGFloat(score) / 100).stroke(accent, style: StrokeStyle(lineWidth: 12, lineCap: .round))
                .rotationEffect(.degrees(-90))
            VStack(spacing: 0) {
                Text("\(score)").font(.system(size: 36, weight: .bold)).monospacedDigit()
                Text("of 100").font(.caption).foregroundStyle(.secondary)
            }
        }.frame(width: 118, height: 118)
    }
}

struct WebView: NSViewRepresentable {
    let url: URL
    func makeNSView(context: Context) -> WKWebView {
        let v = WKWebView()
        v.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
        return v
    }
    func updateNSView(_ nsView: WKWebView, context: Context) {}
}

// MARK: - User manual

/// The manual is docs/MANUAL.md, turned into manual.html inside the app when it is built.
struct ManualView: View {
    static let launchKey = "showManualOnLaunch"
    @AppStorage(ManualView.launchKey) private var showOnLaunch = true
    @Environment(\.dismiss) private var dismiss

    static var url: URL? {
        if let u = Bundle.main.url(forResource: "manual", withExtension: "html") { return u }
        let dev = (Bundle.main.object(forInfoDictionaryKey: "DMVDevProjectPath") as? String) ?? ""
        let u = URL(fileURLWithPath: dev + "/build/obj/manual.html")
        return FileManager.default.fileExists(atPath: u.path) ? u : nil
    }

    var body: some View {
        VStack(spacing: 0) {
            if let u = ManualView.url {
                WebView(url: u)
            } else {
                Text("The user manual is missing from this build.").foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            Divider()
            HStack {
                Toggle("Show this guide when Design My Video opens", isOn: $showOnLaunch)
                Spacer()
                Text("Reopen any time: Help → User Manual").font(.caption).foregroundStyle(.secondary)
                Button("Get started") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            .padding(.horizontal, 18).padding(.vertical, 12)
        }
        .frame(width: 860, height: 680)
    }
}

struct ResultsView: View {
    @EnvironmentObject var model: AppModel
    @State private var showReport = false

    var body: some View {
        if let run = model.selected {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    if let r = model.review { header(run, r); EditBox(); categories(r); suggestions(r) }
                    else { noReview(run) }
                }
                .padding(28)
                .frame(maxWidth: 980)
                .frame(maxWidth: .infinity)
            }
            .safeAreaInset(edge: .bottom) { if model.review != nil { bottomBar(run) } }
            .sheet(isPresented: $showReport) {
                VStack(spacing: 0) {
                    HStack { Text("Report").font(.headline); Spacer(); Button("Done") { showReport = false }.keyboardShortcut(.defaultAction) }.padding(10)
                    WebView(url: run.dir.appendingPathComponent("report.html"))
                }.frame(width: 1040, height: 760)
            }
            .sheet(isPresented: $model.showPlan) { PlanSheet().environmentObject(model) }
            .sheet(isPresented: $model.showRender) { RenderSheet().environmentObject(model) }
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button { model.pendingDeleteRun = run } label: { Label("Move to Trash", systemImage: "trash") }
                        .help("Move this analysis and its edited versions to the Trash")
                        .disabled(model.running)
                }
            }
        }
    }

    func header(_ run: RunSummary, _ r: Review) -> some View {
        HStack(alignment: .top, spacing: 24) {
            ScoreRing(score: r.overallScore)
            VStack(alignment: .leading, spacing: 8) {
                Text(run.name).font(.title.bold()).lineLimit(2)
                if let p = r.providerLabel { Text("Reviewed by \(p)").font(.caption).foregroundStyle(.secondary) }
                if !model.timings.isEmpty {
                    HStack(spacing: 14) {
                        ForEach([("analysis", "Analysis"), ("review", "Evaluation"), ("plan", "Last edit plan"), ("apply", "Last apply")], id: \.0) { key, label in
                            if let s = model.timings[key] {
                                Label("\(label) \(duration(s))", systemImage: "stopwatch").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                Text(r.verdict).fixedSize(horizontal: false, vertical: true)
                Text(r.toneAndMood).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                HStack {
                    Button { showReport = true } label: { Label("Open report", systemImage: "doc.richtext") }
                    Button { NSWorkspace.shared.activateFileViewerSelecting([run.dir]) } label: { Label("Show in Finder", systemImage: "folder") }
                    Menu {
                        ForEach(model.providers) { p in
                            Button(p.label) { model.rerunReview(provider: p.id) }.disabled(!p.available)
                        }
                    } label: { Label("Review again with…", systemImage: "arrow.clockwise") }.fixedSize()
                }.padding(.top, 4)
            }
        }
    }

    func categories(_ r: Review) -> some View {
        GroupBox("Scores") {
            LazyVGrid(columns: [GridItem(.flexible(), spacing: 22), GridItem(.flexible())], alignment: .leading, spacing: 14) {
                ForEach(r.categories) { c in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack { Text(c.category).bold(); Spacer(); Text("\(c.score)/\(c.max)").monospacedDigit() }
                        ProgressView(value: Double(c.score), total: Double(max(1, c.max))).tint(accent)
                        Text(c.rationale).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                }
            }.padding(6)
        }
    }

    func suggestions(_ r: Review) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Suggestions").font(.title2.bold())
                Spacer()
                Button("Approve all") { for s in r.suggestions where !model.approved.contains(s.id) { model.toggle(s.id) } }
                Button("Clear") { for id in model.approved { model.toggle(id) } }
            }
            ForEach(r.suggestions) { s in SuggestionRow(s: s) }
        }
    }

    func noReview(_ run: RunSummary) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(run.name).font(.title.bold())
            Text("This analysis has no review yet.").foregroundStyle(.secondary)
            Menu("Review with…") {
                ForEach(model.providers) { p in Button(p.label) { model.rerunReview(provider: p.id) }.disabled(!p.available) }
            }.fixedSize()
        }
    }

    func bottomBar(_ run: RunSummary) -> some View {
        HStack(spacing: 12) {
            Text(model.approved.isEmpty ? "Tick suggestions to apply them" : "\(model.approved.count) approved")
                .foregroundStyle(.secondary)
            Spacer()
            if let busy = model.busyMessage { ProgressView().controlSize(.small); Text(busy) }
            if let m = model.markersPath {
                Button { NSWorkspace.shared.open(m) } label: { Label("Open markers in Final Cut", systemImage: "film.stack") }
            }
            Button { model.exportMarkers() } label: {
                Label(model.approved.isEmpty ? "Export all as markers" : "Export as markers", systemImage: "mappin.and.ellipse")
            }
            Button { model.planEdits() } label: {
                Label("Apply approved…", systemImage: "wand.and.stars")
            }
            .buttonStyle(.borderedProminent).tint(accent)
            .disabled(model.approved.isEmpty || model.busyMessage != nil)
            .help("Claude turns the approved suggestions into timeline edits and shows them to you before anything changes")
        }
        .padding(.horizontal, 20).padding(.vertical, 12)
        .background(.bar)
    }
}

/// Type an instruction for the AI editor, and see the edited versions made so far.
struct EditBox: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Label("Edit with AI", systemImage: "wand.and.stars").font(.headline)
                    Spacer()
                    Text(model.versions.first(where: { !$0.overwrote }).map { "Builds on v\($0.version) unless you start from the original." }
                         ?? "Edits go into a new project by default; your original stays untouched.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                HStack(alignment: .top) {
                    TextField("e.g. “Duck the music 8 dB under the interviews”, “Cut the second answer”, “Move the last song section to the start”, “Use a better take of her first answer”",
                              text: $model.commandText, axis: .vertical)
                        .lineLimit(2...5)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit { model.planEdits(command: model.commandText) }
                    Button { model.planEdits(command: model.commandText) } label: { Text("Plan edits").padding(.horizontal, 6) }
                        .buttonStyle(.borderedProminent).tint(accent)
                        .disabled(model.commandText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
                HStack(spacing: 10) {
                    Button { model.findFillers() } label: { Label("Remove filler words…", systemImage: "scissors") }
                        .help("Finds every um, uh and er in the dialogue and plans a cut for each. You approve them before anything changes.")
                    Button { model.showRender = true } label: { Label("Render video…", systemImage: "play.rectangle") }
                        .help("Make a video file of the original or any version: a quick preview or a finished file.")
                    Spacer()
                    if let r = model.lastRender {
                        Button { NSWorkspace.shared.open(r) } label: { Label("Play render", systemImage: "play.fill") }
                            .buttonStyle(.borderedProminent).tint(accent)
                        Button { NSWorkspace.shared.activateFileViewerSelecting([r]) } label: { Image(systemName: "folder") }
                            .help("Show in Finder")
                    }
                }
                if !model.versions.isEmpty {
                    Toggle("Start from the original timeline instead of the latest version", isOn: $model.fromOriginal)
                        .toggleStyle(.checkbox).font(.callout)
                }
                if let v = model.justCreated {
                    HStack {
                        Image(systemName: "checkmark.seal.fill").foregroundStyle(.green)
                        Text((v.overwrote ? "Original timeline overwritten" : "New project “v\(v.version)” created") + ": \(v.applied) edit\(v.applied == 1 ? "" : "s") applied"
                             + (v.skipped > 0 ? ", \(v.skipped) skipped" : "") + (v.seconds.map { " in \(duration($0))" } ?? ""))
                        Spacer()
                        Button { NSWorkspace.shared.open(v.path) } label: { Label("Open in Final Cut", systemImage: "film.stack") }
                            .buttonStyle(.borderedProminent).tint(accent)
                    }
                    .padding(10).background(Color.green.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
                }
                if !model.versions.isEmpty {
                    Divider()
                    Text("Versions").font(.subheadline.bold())
                    ForEach(model.versions) { v in
                        HStack(alignment: .firstTextBaseline) {
                            Text(v.overwrote ? "Original" : "v\(v.version)").font(.callout.monospacedDigit().bold())
                                .foregroundStyle(v.overwrote ? Color.red : Color.primary)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(v.summary).font(.callout).lineLimit(2)
                                Text((v.overwrote ? "Overwrote the original · " : "New project · ") + "\(v.applied) edits · \(v.created.replacingOccurrences(of: "T", with: " "))"
                                     + (v.seconds.map { " · took \(duration($0))" } ?? ""))
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button("Open in Final Cut") { NSWorkspace.shared.open(v.path) }
                            Button { NSWorkspace.shared.activateFileViewerSelecting([v.path]) } label: { Image(systemName: "folder") }
                                .help("Show in Finder")
                            Button { model.pendingDeleteVersion = v } label: { Image(systemName: "trash") }
                                .help(v.overwrote ? "Remove this entry from the list" : "Move this version to the Trash")
                        }
                    }
                }
            }.padding(6)
        }
        .alert(model.pendingDeleteVersion?.overwrote == true ? "Remove this entry from the list?" : "Move version \(model.pendingDeleteVersion?.version ?? 0) to the Trash?",
               isPresented: Binding(get: { model.pendingDeleteVersion != nil }, set: { if !$0 { model.pendingDeleteVersion = nil } })) {
            Button(model.pendingDeleteVersion?.overwrote == true ? "Remove" : "Move to Trash", role: .destructive) {
                if let v = model.pendingDeleteVersion { model.trash(v) }; model.pendingDeleteVersion = nil
            }
            Button("Cancel", role: .cancel) { model.pendingDeleteVersion = nil }
        } message: {
            Text(model.pendingDeleteVersion?.overwrote == true
                 ? "Only the entry is removed. The timeline file it overwrote is your own and stays where it is."
                 : "The edited FCPXML for this version goes to the Trash. If you already imported it into Final Cut, the project in your library is not affected.")
        }
    }
}

/// Choose what to render and how.
struct RenderSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var base = "latest"
    @State private var preset = "preview"
    @State private var loudness = "none"

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Render video").font(.title2.bold())
            Text("Makes a video file straight from the timeline, without Final Cut. Picture, titles, captions, transitions, your colour balance and the sound mix are included; some Final Cut effects (LUTs, Motion templates such as chapter cards, Sharpen) are not, and the report lists them. For the final broadcast master, export from Final Cut.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Form {
                Picker("Version", selection: $base) {
                    Text("Latest version").tag("latest")
                    Text("Original timeline").tag("original")
                    ForEach(model.versions.filter { !$0.overwrote }) { v in Text("v\(v.version) · \(v.summary.prefix(50))").tag(v.path.path) }
                }
                Picker("Quality", selection: $preset) {
                    Text("Preview · 720p MP4 (fastest)").tag("preview")
                    Text("HD · 1080p MP4").tag("hd")
                    Text("4K · HEVC MP4").tag("hevc4k")
                    Text("ProRes 422 HQ · full size MOV").tag("prores")
                }
                Picker("Loudness", selection: $loudness) {
                    Text("Leave as mixed").tag("none")
                    Text("Broadcast · −23 LUFS, peaks ≤ −3 dBTP").tag("broadcast")
                    Text("Social media · −14 LUFS, peaks ≤ −1 dBTP").tag("social")
                }
            }.formStyle(.grouped)
            Text("A 10-minute film takes roughly 10–20 minutes as a preview on this Mac; 4K and ProRes take longer.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                Button("Render") { model.render(base: base, preset: preset, loudness: loudness); dismiss() }
                    .keyboardShortcut(.defaultAction).buttonStyle(.borderedProminent).tint(accent)
            }
        }
        .padding(22).frame(width: 560)
    }
}

/// The edits the AI proposes, for the editor to approve one by one before anything is written.
struct PlanSheet: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let p = model.plan {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Review the planned edits").font(.title2.bold())
                    if let c = p.command, !c.isEmpty { Text("“\(c)”").italic() }
                    Text(p.summary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    Text("Nothing changes until you click Apply. The result is a new version; your original timeline is untouched.")
                        .font(.caption).foregroundStyle(.secondary)
                }.padding(20)
                Divider()
                List {
                    ForEach(p.ops) { op in
                        HStack(alignment: .top, spacing: 10) {
                            Toggle("", isOn: Binding(get: { model.planChosen.contains(op.id) },
                                                     set: { on in if on { model.planChosen.insert(op.id) } else { model.planChosen.remove(op.id) } }))
                                .toggleStyle(.checkbox).labelsHidden().disabled(!op.ready)
                            VStack(alignment: .leading, spacing: 3) {
                                HStack(spacing: 6) {
                                    Text(opLabels[op.op] ?? op.op).font(.caption.bold())
                                        .padding(.horizontal, 7).padding(.vertical, 1).background(accent.opacity(0.18), in: Capsule())
                                    if let s = op.suggestionId { Text(s).font(.caption.bold()).foregroundStyle(.secondary) }
                                }
                                Text(op.note).fixedSize(horizontal: false, vertical: true)
                                Text(op.ready ? op.detail : "Can't be applied: \(op.detail)")
                                    .font(.caption).foregroundStyle(op.ready ? Color.secondary : Color.red)
                            }
                        }.padding(.vertical, 4).opacity(op.ready ? 1 : 0.6)
                    }
                    if !p.notPossible.isEmpty {
                        Section("Needs doing by hand") {
                            ForEach(p.notPossible, id: \.self) { Text($0).font(.callout).foregroundStyle(.secondary) }
                        }
                    }
                }
                Divider()
                VStack(alignment: .leading, spacing: 6) {
                    Picker("Save the edits as", selection: $model.overwriteOriginal) {
                        Text("A new project (recommended)").tag(false)
                        Text("Overwrite the original").tag(true)
                    }.pickerStyle(.radioGroup)
                    Text(model.overwriteOriginal
                         ? "The original timeline file will be replaced. You'll be asked to confirm twice."
                         : "Your original project stays exactly as it is. The edits open in Final Cut as a separate project.")
                        .font(.caption).foregroundStyle(model.overwriteOriginal ? Color.red : Color.secondary)
                }.padding(.horizontal, 16).padding(.top, 12)
                HStack {
                    Button("Select all") { model.planChosen = Set(p.ops.filter(\.ready).map(\.id)) }
                    Button("None") { model.planChosen = [] }
                    Spacer()
                    Button("Cancel") { model.showPlan = false }.keyboardShortcut(.cancelAction)
                    Button(model.overwriteOriginal ? "Overwrite original…" : "Apply \(model.planChosen.count) edit\(model.planChosen.count == 1 ? "" : "s") to a new project") {
                        if model.overwriteOriginal { model.overwriteStep = 1 } else { model.applyPlan() }
                    }
                        .buttonStyle(.borderedProminent).tint(model.overwriteOriginal ? .red : accent).keyboardShortcut(.defaultAction)
                        .disabled(model.planChosen.isEmpty)
                }.padding(16)
            }
        }
        .frame(width: 760, height: 700)
        .alert("Overwrite the original timeline?", isPresented: Binding(get: { model.overwriteStep == 1 }, set: { if !$0 && model.overwriteStep == 1 { model.overwriteStep = 0 } })) {
            Button("Cancel", role: .cancel) { model.overwriteStep = 0 }
            Button("Continue") { DispatchQueue.main.async { model.overwriteStep = 2 } }
        } message: {
            Text("Instead of creating a new project, the \(model.planChosen.count) selected edits will be written into your original timeline file. Creating a new project is the safer choice.")
        }
        .alert("This cannot be undone", isPresented: Binding(get: { model.overwriteStep == 2 }, set: { if !$0 && model.overwriteStep == 2 { model.overwriteStep = 0 } })) {
            Button("Cancel", role: .cancel) { model.overwriteStep = 0 }
            Button("Overwrite permanently", role: .destructive) { model.applyPlan(overwrite: true) }
        } message: {
            Text("The original project will not be retrievable after this step. Design My Video keeps no backup of it. Choose Cancel and save as a new project if you might want it back.")
        }
    }
}

struct SuggestionRow: View {
    @EnvironmentObject var model: AppModel
    let s: Suggestion
    @State private var expanded = false

    var sevColor: Color { s.severity == "high" ? .red : s.severity == "medium" ? .orange : .green }

    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            Group {
                if let u = model.frameURL(at: s.startSeconds), let img = NSImage(contentsOf: u) {
                    Image(nsImage: img).resizable().aspectRatio(16 / 9, contentMode: .fill)
                } else {
                    ZStack { Color.primary.opacity(0.06); Image(systemName: "photo").foregroundStyle(.tertiary) }
                }
            }
            .frame(width: 190, height: 107).clipShape(RoundedRectangle(cornerRadius: 8))

            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    Text(s.id).bold()
                    Text("\(timecode(s.startSeconds)) → \(timecode(s.endSeconds))").font(.caption.monospaced()).foregroundStyle(.secondary)
                    Text(s.severity.capitalized).font(.caption.bold()).foregroundStyle(.white)
                        .padding(.horizontal, 7).padding(.vertical, 1).background(sevColor, in: Capsule())
                    Text(s.category).font(.caption).padding(.horizontal, 7).padding(.vertical, 1)
                        .overlay(Capsule().stroke(Color.secondary.opacity(0.4)))
                    Text("+\(s.expectedGain)").font(.caption.bold()).foregroundStyle(accent)
                }
                Text(s.suggestion).font(.headline).fixedSize(horizontal: false, vertical: true)
                Text(s.issue).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                DisclosureGroup("How to do it in Final Cut", isExpanded: $expanded) {
                    Text(s.howInFinalCut).font(.callout).fixedSize(horizontal: false, vertical: true).padding(.top, 4)
                }.font(.callout)
            }
            Spacer(minLength: 8)
            Toggle("Approve", isOn: Binding(get: { model.approved.contains(s.id) }, set: { _ in model.toggle(s.id) }))
                .toggleStyle(.checkbox)
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 12).fill(model.approved.contains(s.id) ? accent.opacity(0.08) : Color.primary.opacity(0.03)))
    }
}

struct SettingsView: View {
    @EnvironmentObject var model: AppModel
    @State private var keys: [String: String] = [:]
    @State private var saved: [String: Bool] = [:]
    @AppStorage("outputDir") private var outputDir = ""

    var body: some View {
        Form {
            Section("AI reviewer") {
                Picker("Default", selection: Binding(get: { model.provider }, set: { model.provider = $0 })) {
                    Text("Automatic (Claude first)").tag("auto")
                    ForEach(model.providers) { p in Text(p.label).tag(p.id) }
                }
                Text("Automatic uses Claude Code if it is installed and signed in, then an Anthropic API key, then OpenAI, Gemini or a local Ollama model.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            ForEach(model.providers) { p in
                Section {
                    HStack {
                        Circle().fill(p.available ? Color.green : Color.secondary.opacity(0.4)).frame(width: 9, height: 9)
                        Text(p.available ? "Ready" : "Not set up").foregroundStyle(.secondary)
                        Spacer()
                        TextField("Model", text: Binding(
                            get: { UserDefaults.standard.string(forKey: "model.\(p.id)") ?? "" },
                            set: { UserDefaults.standard.set($0, forKey: "model.\(p.id)") }),
                                  prompt: Text(p.defaultModel ?? "default")).frame(width: 220)
                    }
                    if keyedProviders.contains(p.id) {
                        HStack {
                            SecureField("API key", text: Binding(get: { keys[p.id] ?? "" }, set: { keys[p.id] = $0 }))
                            Button(saved[p.id] == true ? "Saved" : "Save") {
                                model.saveKey(p.id, keys[p.id] ?? "") { ok in saved[p.id] = ok; if ok { keys[p.id] = "" } }
                            }.disabled((keys[p.id] ?? "").isEmpty)
                            Button("Remove", role: .destructive) { model.deleteKey(p.id) }.disabled(!p.available)
                        }
                        Text("Stored in your Mac's Keychain.").font(.caption).foregroundStyle(.secondary)
                    } else if p.id == "claude_code" {
                        Text(p.available ? "Uses your Claude plan through Claude Code; no key needed."
                                         : "Install Claude Code and sign in; no key needed.")
                            .font(.caption).foregroundStyle(.secondary)
                    } else if p.id == "ollama" {
                        Text("Run Ollama with a vision model (e.g. qwen2.5vl). Free and offline, lower quality.").font(.caption).foregroundStyle(.secondary)
                    }
                } header: { Text(p.label) }
            }
            Section("Help") {
                Toggle("Show the user manual when the app opens", isOn: Binding(
                    get: { UserDefaults.standard.object(forKey: ManualView.launchKey) as? Bool ?? true },
                    set: { UserDefaults.standard.set($0, forKey: ManualView.launchKey); model.objectWillChange.send() }))
                Button("Open User Manual") { model.showManual = true }
            }
            Section("Storage") {
                HStack {
                    Text(Engine.outputDir.path).font(.caption).textSelection(.enabled).lineLimit(2)
                    Spacer()
                    Button("Change…") {
                        let panel = NSOpenPanel(); panel.canChooseDirectories = true; panel.canChooseFiles = false
                        panel.canCreateDirectories = true
                        if panel.runModal() == .OK, let u = panel.url { outputDir = u.path; model.start() }
                    }
                }
                HStack {
                    Text("\(model.runs.count) saved analys\(model.runs.count == 1 ? "is" : "es")").font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button("Move All to Trash…", role: .destructive) { model.confirmDeleteAll = true }
                        .disabled(model.runs.isEmpty || model.running)
                }
            }
            .alert("Move all \(model.runs.count) analyses to the Trash?", isPresented: $model.confirmDeleteAll) {
                Button("Move All to Trash", role: .destructive) { model.trashAllRuns() }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("Every analysis, review and edited version the app made goes to the Trash. Your videos and original Final Cut timelines are not touched.")
            }
        }
        .formStyle(.grouped)
        .frame(width: 560, height: 640)
        .onAppear { model.refreshProviders() }
    }
}
