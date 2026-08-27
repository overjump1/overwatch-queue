import SwiftUI

/// Drives the whole app by hand while there's no PC server to talk to.
///
/// This isn't a mock screen — it pushes real `QueueEvent`s through the real transport
/// into the real store, so what you see here is exactly what the app will do when the
/// events come off a socket instead. Whatever you trigger also mirrors to the watch and
/// to the Live Activity.
struct DebugControlPanel: View {
    @Environment(AppModel.self) private var model
    @Environment(QueueStore.self) private var store
    @Environment(\.dismiss) private var dismiss

    @State private var mode: QueueMode = .quickPlay
    @State private var role: Role = .damage
    @State private var estimate: Double = 90

    private var mock: MockTransport { model.mock }
    private var isMock: Bool { model.source == .mock }

    var body: some View {
        NavigationStack {
            Form {
                sourceSection
                if isMock {
                    queueSection
                    jumpSection
                    scenarioSection
                }
                sentCommandsSection
                catalogSection
                cacheSection
            }
            .navigationTitle("Controls")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    // MARK: - Sections

    @ViewBuilder
    private var sourceSection: some View {
        @Bindable var model = model

        Section {
            Picker("State from", selection: $model.source) {
                ForEach(AppModel.Source.allCases) { Text($0.displayName).tag($0) }
            }
            .pickerStyle(.segmented)

            if model.source == .server {
                LabeledContent("Host") {
                    TextField("192.168.1.10", text: $model.endpoint.host)
                        .multilineTextAlignment(.trailing)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.numbersAndPunctuation)
                }
                LabeledContent("Port") {
                    TextField("8787", value: $model.endpoint.port, format: .number.grouping(.never))
                        .multilineTextAlignment(.trailing)
                        .keyboardType(.numberPad)
                }
                LabeledContent("Status", value: store.status.displayText)
                    .foregroundStyle(store.status.isLive ? Palette.support : Palette.neutral)
            }
        } header: {
            Text("Source")
        } footer: {
            Text(model.source == .mock
                 ? "Mock drives the app from this panel. Nothing leaves the device."
                 : "Connects to ws://\(model.endpoint.displayText)/queue on your PC. See docs/PROTOCOL.md.")
        }
    }

    private var queueSection: some View {
        Section("Queue") {
            Picker("Mode", selection: $mode) {
                ForEach(QueueMode.allCases, id: \.self) { Text($0.displayName).tag($0) }
            }

            if mode.usesRoleQueue {
                Picker("Role", selection: $role) {
                    ForEach(Role.queueable, id: \.self) { Text($0.displayName).tag($0) }
                }
            }

            VStack(alignment: .leading) {
                LabeledContent("Estimated wait", value: QueueTime.compact(estimate))
                Slider(value: $estimate, in: 10...600, step: 5)
                    .onChange(of: estimate) { _, new in mock.setEstimate(new) }
            }

            Button {
                mock.reset()
                mock.apply(.searching(SearchInfo(mode: mode,
                                                 role: mode.usesRoleQueue ? role : .open,
                                                 startedAt: .now,
                                                 estimatedWait: estimate)))
            } label: {
                Label("Start Queue", systemImage: "play.fill")
            }

            if store.phase.kind == .searching {
                Button {
                    mock.addElapsed(60)
                } label: {
                    Label("Skip ahead 1 minute", systemImage: "forward.fill")
                }
                Button {
                    mock.addElapsed(300)
                } label: {
                    Label("Skip ahead 5 minutes", systemImage: "forward.end.fill")
                }
            }
        }
    }

    private var jumpSection: some View {
        Section {
            jumpButton("Match Found", "bolt.fill") {
                .matchFound(MatchFoundInfo(mode: mode,
                                           role: mode.usesRoleQueue ? role : .open,
                                           waited: elapsedNow(),
                                           lockInAt: .now + 15))
            }
            jumpButton("Map Vote", "map.fill") {
                .mapVote(MapVoteInfo(options: store.catalog.mapVoteOptions(for: mode),
                                     deadline: .now + 25))
            }
            jumpButton("Hero Select", "person.crop.square.fill") {
                .heroSelect(HeroSelectInfo(mode: mode,
                                           role: mode.usesRoleQueue ? role : .open,
                                           mapKey: currentOrRandomMapKey(),
                                           deadline: .now + 40,
                                           takenHeroKeys: randomTakenHeroes()))
            }
            jumpButton("In Game", "gamecontroller.fill") {
                .inGame(InGameInfo(mode: mode,
                                   mapKey: currentOrRandomMapKey(),
                                   heroKey: store.currentHero?.key,
                                   startedAt: .now))
            }
            jumpButton("Cancelled", "xmark.circle.fill") {
                .cancelled(CancelInfo(reason: .matchCancelled))
            }

            Button(role: .destructive) {
                mock.reset()
            } label: {
                Label("Reset to Idle", systemImage: "arrow.counterclockwise")
            }
        } header: {
            Text("Jump to phase")
        } footer: {
            Text("Greyed-out steps aren't reachable from \(store.phase.kind.rawValue) — the same transition rules the real server has to follow.")
        }
    }

    private var scenarioSection: some View {
        Section {
            ForEach(MockTransport.Scenario.allCases) { scenario in
                Button {
                    mock.play(scenario,
                              catalog: store.catalog.maps(for: mode).map(\.key),
                              heroes: store.catalog.heroes(role: role, mode: mode).map(\.key))
                } label: {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(scenario.displayName)
                        Text(scenario.detail)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            if mock.isRunningScenario {
                Button(role: .destructive) {
                    mock.cancelScenario()
                } label: {
                    Label("Stop scenario", systemImage: "stop.fill")
                }
            }
        } header: {
            Text("Scenarios")
        } footer: {
            Text("Plays a timeline so you can watch the real transitions and animations end to end, rather than jumping between them.")
        }
    }

    @ViewBuilder
    private var sentCommandsSection: some View {
        if !mock.sentCommands.isEmpty {
            Section {
                ForEach(Array(mock.sentCommands.enumerated().reversed()), id: \.offset) { _, command in
                    Text(describe(command))
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                }
            } header: {
                Text("Sent upstream")
            } footer: {
                Text("What your PC server would have received. Votes and hero picks go out as commands.")
            }
        }
    }

    private var catalogSection: some View {
        Section("Catalog") {
            LabeledContent("Source", value: store.catalog.source.displayText)
            LabeledContent("Heroes", value: "\(store.catalog.heroes.count)")
            LabeledContent("Maps", value: "\(store.catalog.maps.count)")
            if let error = store.catalog.lastError {
                Text(error).font(.caption).foregroundStyle(Palette.damage)
            }
            Button {
                Task { await store.catalog.refresh() }
            } label: {
                Label("Refresh from OverFast", systemImage: "arrow.clockwise")
            }
            .disabled(store.catalog.isLoading)
        }
    }

    private var cacheSection: some View {
        Section {
            LabeledContent("On disk", value: byteText(ImageCache.shared.diskUsage))
            LabeledContent("In memory", value: byteText(ImageCache.shared.memoryUsage))
            Button(role: .destructive) {
                ImageCache.shared.clear()
            } label: {
                Label("Clear image cache", systemImage: "trash")
            }
        } header: {
            Text("Art cache")
        } footer: {
            Text("Hero portraits and map screenshots are cached on disk and kept decoded in memory, so they load once and then come back instantly — including after a relaunch.")
        }
    }

    private func byteText(_ bytes: Int) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)
    }

    // MARK: - Helpers

    private func jumpButton(_ title: String, _ symbol: String,
                            _ phase: @escaping () -> QueuePhase) -> some View {
        let target = phase()
        let allowed = store.phase.canTransition(to: target)
        return Button {
            mock.cancelScenario()
            mock.apply(target)
        } label: {
            Label(title, systemImage: symbol)
        }
        .disabled(!allowed)
    }

    private func elapsedNow() -> TimeInterval {
        if case .searching(let info) = store.phase { return info.elapsed() }
        return estimate
    }

    private func currentOrRandomMapKey() -> String? {
        if let existing = store.currentMap?.key { return existing }
        return store.catalog.maps(for: mode).randomElement()?.key
    }

    private func randomTakenHeroes() -> [String] {
        Array(store.catalog.heroes.shuffled().prefix(3).map(\.key))
    }

    private func describe(_ command: ClientCommand) -> String {
        switch command {
        case .hello(let c): return "hello(\(c.kind.rawValue))"
        case .voteMap(let k): return "voteMap(\(k))"
        case .selectHero(let k): return "selectHero(\(k))"
        case .cancelQueue: return "cancelQueue"
        case .requestSnapshot: return "requestSnapshot"
        }
    }
}
