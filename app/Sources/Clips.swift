import AppKit
import AVKit
import SwiftUI
import UniformTypeIdentifiers

// MARK: - Clips data

struct ClipItem: Identifiable, Hashable {
    let id: String
    let start: Double
    let end: Double
    let score: Int
    let scores: [String: Int]
    let title: String
    let reason: String
    let hashtags: [String]
    let status: String
    let file: URL?
    let thumb: URL?
    let text: String
    var length: Double { end - start }
}

struct ClipProject: Identifiable, Hashable {
    var id: URL { dir }
    let dir: URL
    let name: String
    let date: Date
    let status: String
    let video: String
    let duration: Double
    let picker: String
    let settings: [String: String]
    let clips: [ClipItem]

    static func load(_ dir: URL) -> ClipProject? {
        guard let data = try? Data(contentsOf: dir.appendingPathComponent("project.json")),
              let o = try? JSONSerialization.jsonObject(with: data) as? [String: Any], (o["kind"] as? String) == "clips" else { return nil }
        let date = (try? dir.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
        var settings: [String: String] = [:]
        for (k, v) in (o["settings"] as? [String: Any]) ?? [:] { settings[k] = "\(v)" }
        let clips: [ClipItem] = ((o["clips"] as? [[String: Any]]) ?? []).map { c in
            let f = (c["file"] as? String).map { URL(fileURLWithPath: $0) }
            return ClipItem(id: c["id"] as? String ?? "?", start: c["start"] as? Double ?? 0, end: c["end"] as? Double ?? 0,
                            score: c["score"] as? Int ?? 0, scores: c["scores"] as? [String: Int] ?? [:],
                            title: c["title"] as? String ?? "", reason: c["reason"] as? String ?? "",
                            hashtags: c["hashtags"] as? [String] ?? [], status: c["status"] as? String ?? "",
                            file: f.flatMap { FileManager.default.fileExists(atPath: $0.path) ? $0 : nil },
                            thumb: (c["thumb"] as? String).map { URL(fileURLWithPath: $0) },
                            text: c["text"] as? String ?? "")
        }
        let src = o["source"] as? [String: Any] ?? [:]
        return ClipProject(dir: dir, name: o["name"] as? String ?? dir.lastPathComponent, date: date,
                           status: o["status"] as? String ?? "", video: o["video"] as? String ?? "",
                           duration: src["duration"] as? Double ?? 0, picker: o["picker"] as? String ?? "",
                           settings: settings, clips: clips)
    }
}

enum Screen { case home, newEdit, newClips, run, clips }

let clipStyles: [(String, String)] = [("bold", "Bold pop"), ("karaoke", "Highlight box"), ("typewriter", "Typewriter"), ("minimal", "Minimal")]
let clipLengths: [(String, Double, Double)] = [("Under 30 s", 12, 30), ("30–60 s", 30, 60), ("60–90 s", 60, 90), ("Auto (20–60 s)", 20, 60)]

// MARK: - Model

extension AppModel {
    static var clipsHome: URL { Engine.outputDir.appendingPathComponent("Clips") }

    func loadClipProjects() {
        let fm = FileManager.default
        let dirs = (try? fm.contentsOfDirectory(at: AppModel.clipsHome, includingPropertiesForKeys: [.contentModificationDateKey])) ?? []
        clipProjects = dirs.compactMap { ClipProject.load($0) }.sorted { $0.date > $1.date }
        if let cur = currentClips { currentClips = clipProjects.first { $0.dir == cur.dir } ?? ClipProject.load(cur.dir) }
    }

    func openClips(_ p: ClipProject) {
        select(nil)
        currentClips = ClipProject.load(p.dir) ?? p
        screen = .clips
    }

    func findClips(video: URL, count: Int, length: Int, aspect: String, layout: String, style: String, hook: Bool, prompt: String) {
        let (_, lo, hi) = clipLengths[max(0, min(clipLengths.count - 1, length))]
        var args = ["clips", "find", "--video", video.path, "--count", "\(count)", "--min", "\(Int(lo))", "--max", "\(Int(hi))",
                    "--aspect", aspect, "--layout", layout, "--style", style, "--hook", hook ? "1" : "0",
                    "--whisper", whisper, "--provider", provider]
        if let m = model(for: provider) { args += ["--model", m] }
        let p = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        if !p.isEmpty { args += ["--prompt", p] }
        runClipJob(args, title: "Making clips")
    }

    func rerenderClips(_ p: ClipProject, ids: [String]? = nil, style: String? = nil, aspect: String? = nil, layout: String? = nil,
                       hook: Bool? = nil, start: Double? = nil, end: Double? = nil) {
        var args = ["clips", "render", p.dir.path]
        if let ids { args += ["--ids", ids.joined(separator: ",")] }
        if let style { args += ["--style", style] }
        if let aspect { args += ["--aspect", aspect] }
        if let layout { args += ["--layout", layout] }
        if let hook { args += ["--hook", hook ? "1" : "0"] }
        if let start { args += ["--start", String(format: "%.2f", start)] }
        if let end { args += ["--end", String(format: "%.2f", end)] }
        pendingClipDir = p.dir
        runClipJob(args, title: "Rendering clips")
    }

    /// Export finished clips into a folder the user picks, at original quality (a copy) or as smaller
    /// social-sized files (re-encoded). Each clip also gets a .txt with its title and hashtags.
    func exportClips(_ p: ClipProject, ids: [String]?, quality: String) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.canCreateDirectories = true
        let n = ids?.count ?? p.clips.filter { $0.file != nil }.count
        panel.prompt = n == 1 ? "Export here" : "Export \(n) clips here"
        panel.message = "Choose where to save the clips."
        guard panel.runModal() == .OK, let base = panel.url else { return }
        let dest = ids?.count == 1 ? base : base.appendingPathComponent(p.name + " – clips")
        var args = ["clips", "export", p.dir.path, "--dest", dest.path, "--quality", quality]
        if let ids { args += ["--ids", ids.joined(separator: ",")] }
        pendingClipDir = p.dir
        revealAfter = dest
        runClipJob(args, title: quality == "small" ? "Exporting smaller files" : "Exporting clips")
    }

    private func runClipJob(_ args: [String], title: String) {
        running = true; percent = 0; note = "Starting…"; stage = title; errorMessage = nil; startedAt = Date()
        process = Engine.run(args, onEvent: { [weak self] ev in
            guard let self else { return }
            switch ev["event"] as? String {
            case "progress":
                if let l = ev["label"] as? String { self.stage = l }
                if let p = ev["percent"] as? Double { self.percent = p } else if let p = ev["percent"] as? Int { self.percent = Double(p) }
                if let n = ev["note"] as? String { self.note = n }
            case "clipproject":
                if let d = ev["dir"] as? String { self.pendingClipDir = URL(fileURLWithPath: d) }
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
            self.loadClipProjects()
            if let d = self.pendingClipDir, let p = ClipProject.load(d) { self.openClips(p) }
            self.pendingClipDir = nil
            if code == 0, let r = self.revealAfter { NSWorkspace.shared.open(r) }
            self.revealAfter = nil
        })
    }

    func trashClips(_ p: ClipProject) {
        try? FileManager.default.trashItem(at: p.dir, resultingItemURL: nil)
        if currentClips?.dir == p.dir { currentClips = nil; screen = .home }
        loadClipProjects()
    }
}

// MARK: - Home

struct HomeView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Design My Video").font(.system(size: 32, weight: .bold))
                    Text("Two ways to work. Pick one; you can switch any time from the sidebar.")
                        .foregroundStyle(.secondary)
                }
                HStack(alignment: .top, spacing: 18) {
                    HomeCard(icon: "wand.and.stars", title: "Review & edit an edit",
                             text: "Drop in a finished edit and its Final Cut timeline. Get a score out of 100, design notes, and approved edits made for you in a new version: titles, sound, colour, cuts, fillers, re-ordering, renders.",
                             button: "New analysis") { model.select(nil); model.screen = .newEdit }
                    HomeCard(icon: "scissors.badge.ellipsis", title: "Make clips from a long video",
                             text: "Drop in an interview, podcast, talk or vlog. The app finds the moments that work on their own, scores them, reframes them to vertical with the speaker centred, adds animated captions and a hook title, and renders them ready to post.",
                             button: "New clips") { model.select(nil); model.screen = .newClips }
                }
                if !model.clipProjects.isEmpty || !model.runs.isEmpty {
                    Text("Recent").font(.headline).padding(.top, 6)
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(model.clipProjects.prefix(3)) { p in
                            Button { model.openClips(p) } label: {
                                Label("\(p.name) · \(p.clips.count) clips", systemImage: "scissors")
                            }.buttonStyle(.link)
                        }
                        ForEach(model.runs.prefix(3)) { r in
                            Button { model.select(r); model.screen = .run } label: {
                                Label(r.name + (r.score.map { " · \($0)/100" } ?? ""), systemImage: "wand.and.stars")
                            }.buttonStyle(.link)
                        }
                    }
                }
            }
            .padding(32).frame(maxWidth: 900).frame(maxWidth: .infinity)
        }
    }
}

struct HomeCard: View {
    let icon: String, title: String, text: String, button: String
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Image(systemName: icon).font(.system(size: 30)).foregroundStyle(accent)
            Text(title).font(.title2.bold())
            Text(text).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 4)
            Button(action: action) { Text(button).padding(.horizontal, 12).padding(.vertical, 4) }
                .buttonStyle(.borderedProminent).tint(accent).controlSize(.large)
        }
        .padding(22)
        .frame(maxWidth: .infinity, minHeight: 280, alignment: .topLeading)
        .background(RoundedRectangle(cornerRadius: 16).fill(Color.primary.opacity(hover ? 0.07 : 0.04)))
        .overlay(RoundedRectangle(cornerRadius: 16).strokeBorder(hover ? accent.opacity(0.6) : Color.primary.opacity(0.08)))
        .onHover { hover = $0 }
        .onTapGesture(perform: action)
    }
}

// MARK: - New clips

struct NewClipsView: View {
    @EnvironmentObject var model: AppModel
    @State private var video: URL?
    @AppStorage("clips.count") private var count = 8
    @AppStorage("clips.length") private var length = 3
    @AppStorage("clips.aspect") private var aspect = "9:16"
    @AppStorage("clips.layout") private var layout = "fill"
    @AppStorage("clips.style") private var style = "bold"
    @AppStorage("clips.hook") private var hook = true
    @State private var prompt = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Make clips").font(.system(size: 30, weight: .bold))
                    Text("Drop in a long video. The app listens to it, picks the moments that stand on their own, scores each one, and renders them as short clips with the speaker centred and captions that follow every word.")
                        .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                DropZone(title: "Long video", subtitle: "MP4 or MOV · an interview, podcast, talk or vlog", icon: "film",
                         types: [.movie, .mpeg4Movie, .quickTimeMovie], url: $video)
                GroupBox {
                    Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 12) {
                        GridRow {
                            Text("Clips")
                            Stepper("\(count) clips", value: $count, in: 1...20).frame(maxWidth: 200, alignment: .leading)
                        }
                        GridRow {
                            Text("Length")
                            Picker("", selection: $length) { ForEach(0..<clipLengths.count, id: \.self) { Text(clipLengths[$0].0).tag($0) } }
                                .pickerStyle(.segmented).labelsHidden().frame(maxWidth: 460)
                        }
                        GridRow {
                            Text("Shape")
                            Picker("", selection: $aspect) {
                                Text("9:16 vertical").tag("9:16"); Text("1:1 square").tag("1:1"); Text("4:5 portrait").tag("4:5"); Text("16:9 wide").tag("16:9")
                            }.pickerStyle(.segmented).labelsHidden().frame(maxWidth: 460)
                        }
                        GridRow {
                            Text("Framing")
                            Picker("", selection: $layout) {
                                Text("Follow the speaker").tag("fill"); Text("Whole picture, blurred fill").tag("fit")
                            }.pickerStyle(.segmented).labelsHidden().frame(maxWidth: 460)
                        }
                        GridRow {
                            Text("Captions")
                            Picker("", selection: $style) { ForEach(clipStyles, id: \.0) { Text($0.1).tag($0.0) } }
                                .pickerStyle(.segmented).labelsHidden().frame(maxWidth: 460)
                        }
                        GridRow {
                            Text("Hook title")
                            Toggle("Show the clip's hook title for its first 3 seconds", isOn: $hook).toggleStyle(.checkbox)
                        }
                        GridRow {
                            Text("Focus")
                            TextField("Optional: what the clips should be about, e.g. “her mother”, “funny moments”, “advice for new artists”",
                                      text: $prompt, axis: .vertical).lineLimit(1...3).frame(maxWidth: 460)
                        }
                        GridRow {
                            Text("AI picker")
                            Picker("", selection: Binding(get: { model.provider }, set: { model.provider = $0 })) {
                                Text("Automatic (Claude first)").tag("auto")
                                ForEach(model.providers) { p in Text(p.label + (p.available ? "" : " — not set up")).tag(p.id) }
                            }.labelsHidden().frame(maxWidth: 320)
                        }
                        GridRow {
                            Text("Transcript")
                            Picker("", selection: $model.whisper) {
                                Text("Fast").tag("base"); Text("Balanced").tag("small"); Text("Best (slow)").tag("medium")
                            }.pickerStyle(.segmented).labelsHidden().frame(maxWidth: 320)
                        }
                    }.padding(6)
                }
                HStack {
                    Text("Everything runs on this Mac except the AI picker, which reads the transcript only.")
                        .font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button {
                        if let v = video { model.findClips(video: v, count: count, length: length, aspect: aspect, layout: layout, style: style, hook: hook, prompt: prompt) }
                    } label: { Label("Find clips", systemImage: "scissors").padding(.horizontal, 14).padding(.vertical, 6) }
                    .buttonStyle(.borderedProminent).tint(accent).controlSize(.large).disabled(video == nil)
                }
            }
            .padding(28).frame(maxWidth: 860).frame(maxWidth: .infinity)
        }
    }
}

// MARK: - Clip project

struct ClipsView: View {
    @EnvironmentObject var model: AppModel
    let project: ClipProject
    @State private var open: ClipItem?
    @State private var confirmTrash = false
    @State private var exportAll = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack(alignment: .firstTextBaseline) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(project.name).font(.system(size: 26, weight: .bold)).lineLimit(1)
                        Text("\(project.clips.count) clips from \(duration(project.duration)) · picked by \(project.picker.isEmpty ? "—" : project.picker) · \(project.settings["aspect"] ?? "9:16") · captions: \(clipStyles.first { $0.0 == project.settings["style"] }?.1 ?? "Bold pop")")
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Menu {
                        Section("Caption style") { ForEach(clipStyles, id: \.0) { s in Button(s.1) { model.rerenderClips(project, style: s.0) } } }
                        Section("Shape") { ForEach(["9:16", "1:1", "4:5", "16:9"], id: \.self) { a in Button(a) { model.rerenderClips(project, aspect: a) } } }
                        Section("Framing") {
                            Button("Follow the speaker") { model.rerenderClips(project, layout: "fill") }
                            Button("Whole picture, blurred fill") { model.rerenderClips(project, layout: "fit") }
                        }
                        Section("Hook title") {
                            Button("Show hook titles") { model.rerenderClips(project, hook: true) }
                            Button("No hook titles") { model.rerenderClips(project, hook: false) }
                        }
                    } label: { Label("Restyle all", systemImage: "paintbrush") }.fixedSize()
                    Button { exportAll = true } label: { Label("Export all…", systemImage: "square.and.arrow.down") }
                        .buttonStyle(.borderedProminent).tint(accent)
                        .disabled(!project.clips.contains { $0.file != nil })
                    Button(role: .destructive) { confirmTrash = true } label: { Image(systemName: "trash") }.help("Move this clip project to the Trash")
                }
                if project.clips.isEmpty {
                    Text(project.status == "failed" ? "This run stopped before clips were made." : "No clips yet.").foregroundStyle(.secondary)
                }
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 200, maximum: 260), spacing: 18)], alignment: .leading, spacing: 20) {
                    ForEach(project.clips) { c in
                        ClipCard(clip: c).onTapGesture { open = c }
                    }
                }
            }
            .padding(26)
        }
        .sheet(item: $open) { c in ClipDetail(project: project, clip: c).environmentObject(model) }
        .sheet(isPresented: $exportAll) { ExportSheet(project: project, ids: nil).environmentObject(model) }
        .alert("Move “\(project.name)” to the Trash?", isPresented: $confirmTrash) {
            Button("Move to Trash", role: .destructive) { model.trashClips(project) }
            Button("Cancel", role: .cancel) {}
        } message: { Text("The clips and their project go to the Trash. Your original video is not touched.") }
    }
}

struct ClipCard: View {
    let clip: ClipItem
    @State private var hover = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ZStack(alignment: .topLeading) {
                Group {
                    if let t = clip.thumb, let img = NSImage(contentsOf: t) {
                        Image(nsImage: img).resizable().aspectRatio(contentMode: .fill)
                    } else {
                        Rectangle().fill(Color.primary.opacity(0.06))
                            .overlay(Image(systemName: clip.status == "failed" ? "exclamationmark.triangle" : "hourglass").foregroundStyle(.secondary))
                    }
                }
                .frame(height: 300).clipped()
                Text("\(clip.score)").font(.system(size: 15, weight: .bold).monospacedDigit())
                    .padding(.horizontal, 9).padding(.vertical, 4)
                    .background(clip.score >= 80 ? accent : Color.black.opacity(0.6), in: Capsule())
                    .foregroundStyle(.white).padding(8)
                if clip.file != nil {
                    Image(systemName: "play.circle.fill").font(.system(size: 40)).foregroundStyle(.white.opacity(hover ? 0.95 : 0.7))
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .frame(height: 300)
            .clipShape(RoundedRectangle(cornerRadius: 12))
            Text(clip.title.isEmpty ? clip.id : clip.title).font(.headline).lineLimit(2)
            Text("\(duration(clip.length)) · from \(timecodeShort(clip.start))" + (clip.status == "ready" ? "" : " · \(clip.status)"))
                .font(.caption).foregroundStyle(.secondary)
        }
        .contentShape(Rectangle())
        .onHover { hover = $0 }
    }
}

/// Chosen before every export: keep the rendered quality, or make smaller files for posting.
struct ExportSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let project: ClipProject
    let ids: [String]?
    @AppStorage("clips.exportQuality") private var quality = "original"

    var clips: [ClipItem] { project.clips.filter { $0.file != nil && (ids == nil || ids!.contains($0.id)) } }
    var originalMB: Double {
        clips.reduce(0) { sum, c in
            sum + Double(((try? c.file!.resourceValues(forKeys: [.fileSizeKey]))?.fileSize) ?? 0) / 1_000_000
        }
    }
    var smallMB: Double { clips.reduce(0) { $0 + $1.length * 3.16 / 8 } }   // ≈ 3 Mbit/s video + 160 kbit/s audio

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(clips.count == 1 ? "Export clip" : "Export \(clips.count) clips").font(.title2.bold())
            Picker("", selection: $quality) {
                VStack(alignment: .leading) {
                    Text("Original quality")
                    Text("Exactly as rendered · about \(Int(originalMB.rounded())) MB in total").font(.caption).foregroundStyle(.secondary)
                }.tag("original")
                VStack(alignment: .leading) {
                    Text("Smaller files for social media")
                    Text("About \(Int(smallMB.rounded())) MB in total (about 25 MB a minute) · looks the same once posted, since platforms re-compress uploads · takes about 1½ minutes per minute of clip on this Mac")
                        .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }.tag("small")
            }
            .pickerStyle(.radioGroup).labelsHidden()
            Text("Each clip is saved with a text file holding its title and hashtags.").font(.caption).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                Button("Choose where…") {
                    dismiss()
                    DispatchQueue.main.async { model.exportClips(project, ids: ids, quality: quality) }
                }
                .keyboardShortcut(.defaultAction).buttonStyle(.borderedProminent).tint(accent).disabled(clips.isEmpty)
            }
        }
        .padding(22).frame(width: 500)
    }
}

func timecodeShort(_ s: Double) -> String { String(format: "%d:%02d", Int(s) / 60, Int(s) % 60) }

struct ClipDetail: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let project: ClipProject
    let clip: ClipItem
    @State private var start: Double = 0
    @State private var end: Double = 0
    @State private var copied = false
    @State private var exporting = false

    var body: some View {
        HStack(alignment: .top, spacing: 22) {
            Group {
                if let f = clip.file {
                    VideoPlayer(player: AVPlayer(url: f))
                } else {
                    Rectangle().fill(Color.black).overlay(Text("Not rendered yet").foregroundStyle(.secondary))
                }
            }
            .frame(width: 340, height: 604).clipShape(RoundedRectangle(cornerRadius: 12))
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    HStack(alignment: .firstTextBaseline) {
                        Text("\(clip.score)").font(.system(size: 34, weight: .bold).monospacedDigit()).foregroundStyle(accent)
                        Text("/ 100").foregroundStyle(.secondary)
                        Spacer()
                        Button("Done") { dismiss() }.keyboardShortcut(.cancelAction)
                    }
                    Text(clip.title).font(.title2.bold()).fixedSize(horizontal: false, vertical: true)
                    ForEach([("hook", "Hook"), ("flow", "Flow"), ("value", "Value"), ("shareability", "Shareability")], id: \.0) { k, label in
                        HStack {
                            Text(label).frame(width: 100, alignment: .leading)
                            ProgressView(value: Double(clip.scores[k] ?? 0), total: 25).tint(accent)
                            Text("\(clip.scores[k] ?? 0)/25").font(.caption.monospacedDigit()).frame(width: 44)
                        }
                    }
                    if !clip.reason.isEmpty { Text(clip.reason).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true) }
                    if !clip.hashtags.isEmpty {
                        HStack {
                            Text(clip.hashtags.joined(separator: " ")).font(.callout).foregroundStyle(accent)
                            Button(copied ? "Copied" : "Copy caption") {
                                let pb = NSPasteboard.general; pb.clearContents()
                                pb.setString(clip.title + "\n\n" + clip.hashtags.joined(separator: " "), forType: .string)
                                copied = true
                            }.controlSize(.small)
                        }
                    }
                    GroupBox("Transcript") {
                        Text(clip.text).font(.callout).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                    }
                    GroupBox("Adjust") {
                        VStack(alignment: .leading, spacing: 8) {
                            Stepper("Starts at \(timecodeShort(start)).\(Int((start * 10).truncatingRemainder(dividingBy: 10)))", value: $start, in: 0...max(0, end - 3), step: 0.5)
                            Stepper("Ends at \(timecodeShort(end)).\(Int((end * 10).truncatingRemainder(dividingBy: 10))) · \(duration(end - start))", value: $end, in: (start + 3)...max(start + 3, project.duration), step: 0.5)
                            Button("Re-render this clip") {
                                model.rerenderClips(project, ids: [clip.id], start: start, end: end); dismiss()
                            }.disabled(abs(start - clip.start) < 0.01 && abs(end - clip.end) < 0.01 && clip.file != nil)
                        }.padding(4)
                    }
                    HStack {
                        Button { exporting = true } label: { Label("Export clip…", systemImage: "square.and.arrow.down") }
                            .buttonStyle(.borderedProminent).tint(accent).disabled(clip.file == nil)
                        if let f = clip.file {
                            Button { NSWorkspace.shared.activateFileViewerSelecting([f]) } label: { Label("Show in Finder", systemImage: "folder") }
                        }
                    }
                }
            }
        }
        .padding(22)
        .frame(width: 860, height: 660)
        .onAppear { start = clip.start; end = clip.end }
        .sheet(isPresented: $exporting) { ExportSheet(project: project, ids: [clip.id]).environmentObject(model) }
    }
}
