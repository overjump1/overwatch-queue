import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var model: AppModel
    @State private var showScanner = false
    @State private var showAbout = false
    @State private var pickingMode = false
    @State private var flash = false

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            if model.pairID == nil && !model.manual {
                PairView(resetNotice: model.pairingWasReset,
                         onCode: { model.pair(with: $0) },
                         onTimeItHere: { pickingMode = true })
            } else {
                queue
            }
        }
        .tint(.white)
        // Worth keeping the screen on for a queue you're watching, and not worth it once the
        // queue is over, so the phone is allowed to sleep again.
        .onChange(of: model.status.state, initial: true) { _, state in
            UIApplication.shared.isIdleTimerDisabled = state != .idle
        }
        .sheet(isPresented: $showScanner) {
            PairView(resetNotice: false) { code in
                model.pair(with: code)
                showScanner = false
            }
            .preferredColorScheme(.dark)
        }
        .sheet(isPresented: $showAbout) {
            AboutView().preferredColorScheme(.dark)
        }
        .confirmationDialog("Which queue are you starting?", isPresented: $pickingMode, titleVisibility: .visible) {
            ForEach(GameMode.allCases, id: \.self) { mode in
                Button(mode.name) { model.startManualQueue(mode: mode) }
            }
            Button("Cancel", role: .cancel) {}
        }
    }

    private var queue: some View {
        let status = model.status
        return ZStack {
            RadialGradient(colors: [status.accent.opacity(status.state == .idle ? 0.08 : 0.28), .clear],
                           center: .center, startRadius: 10, endRadius: 420)
                .ignoresSafeArea()
                .animation(.easeInOut(duration: 0.6), value: status)

            Color.green.opacity(flash ? 0.45 : 0).ignoresSafeArea().allowsHitTesting(false)

            VStack(spacing: 18) {
                HStack {
                    Spacer()
                    Menu {
                        if !model.manual && status.state == .idle {
                            Button("Time a queue here", systemImage: "stopwatch") { pickingMode = true }
                        }
                        if Updates.checksForUpdates {
                            Button("Check for updates", systemImage: "arrow.down.circle") {
                                model.checkForUpdate(force: true)
                            }
                        }
                        Button("Scan new code", systemImage: "qrcode.viewfinder") { showScanner = true }
                        if model.pairID != nil {
                            Button("Unpair", systemImage: "xmark.circle", role: .destructive) { model.unpair() }
                        }
                        Button("About", systemImage: "info.circle") { showAbout = true }
                    } label: {
                        Image(systemName: "gearshape.fill")
                            .font(.title3)
                            .foregroundStyle(.secondary)
                            .padding(12)
                    }
                }
                Spacer()
                ModeBadge(status: status, size: 132)
                    .scaleEffect(flash ? 1.12 : 1)
                VStack(spacing: 6) {
                    Text(status.title)
                        .font(.system(size: 34, weight: .bold, design: .rounded))
                        .foregroundStyle(status.state == .idle ? .white : status.accent)
                    Text(status.subtitle)
                        .font(.title3.weight(.medium))
                        .foregroundStyle(.secondary)
                }
                if status.state != .idle {
                    ElapsedText(status: status)
                        .font(.system(size: 76, weight: .semibold, design: .rounded))
                        .monospacedDigit()
                        .foregroundStyle(status.state == .found ? .secondary : .primary)
                }
                if status.state == .found {
                    Text(model.manual ? "Good luck out there" : "Head back to your PC")
                        .font(.headline)
                        .foregroundStyle(.secondary)
                } else if status.state == .playing, let mode = status.mode {
                    Text("\(mode.name) · in match")
                        .font(.headline)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if model.manual {
                    manualControls(status)
                }
                updateLine
                if !model.reachable && !model.manual {
                    Label("Can't reach the server — retrying", systemImage: "wifi.exclamationmark")
                        .font(.footnote)
                        .foregroundStyle(.orange)
                }
            }
            .padding()
        }
        .onChange(of: model.matchAlerts) { _, _ in
            withAnimation(.easeOut(duration: 0.15)) { flash = true }
            withAnimation(.easeIn(duration: 0.9).delay(0.25)) { flash = false }
        }
    }

    /// A queue the phone is timing itself: the two things a PC would otherwise be saying.
    private func manualControls(_ status: QueueStatus) -> some View {
        VStack(spacing: 12) {
            if status.state == .queueing {
                Button {
                    model.manualMatchFound()
                } label: {
                    Label("Match found", systemImage: "checkmark.circle.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            }
            Button(status.state == .queueing ? "Cancel queue" : "Done", role: .cancel) {
                model.endManualQueue()
            }
            .buttonStyle(.bordered)
            Text(model.pairID == nil
                 ? "Timed on this iPhone. Pair a PC and it starts and stops on its own."
                 : "Timed on this iPhone. Your PC takes over again once this one ends.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .padding(.horizontal, 32)
    }

    /// iOS can't install an app on itself, so this only says a newer build is out. Only the
    /// sideloaded build ever shows it, since only its updates are on GitHub (Updates.swift).
    @ViewBuilder
    private var updateLine: some View {
        if let (text, icon, colour) = updateLabel {
            Label(text, systemImage: icon).font(.footnote).foregroundStyle(colour)
        }
    }

    private var updateLabel: (String, String, Color)? {
        switch model.updateNotice {
        case .available(let version): return ("Update available · \(version)", "arrow.down.circle", .green)
        case .checking: return ("Checking for updates…", "arrow.down.circle", .secondary)
        case .upToDate: return ("You're on the latest version", "checkmark.circle", .secondary)
        case .failed: return ("Couldn't check for updates", "exclamationmark.circle", .orange)
        case .quiet: return nil
        }
    }
}

/// What this is, what it isn't, and where the privacy policy lives — the last of which the App
/// Store wants reachable from inside the app, not only from the listing.
struct AboutView: View {
    @Environment(\.dismiss) private var dismiss

    private static let privacyPolicy =
        URL(string: "https://github.com/overjump1/overwatch-queue/blob/main/docs/privacy.md")!

    var body: some View {
        NavigationStack {
            List {
                Section {
                    LabeledContent("Version", value: Updates.current)
                } footer: {
                    Text("Shows how long you've been in a queue, and tells you when a match is found.")
                }
                Section {
                    Link(destination: Self.privacyPolicy) {
                        Label("Privacy policy", systemImage: "hand.raised")
                    }
                }
                Section {
                    Text("""
                    This app is not affiliated with Overwatch or Blizzard Entertainment.

                    Overwatch and the Overwatch logo are ©2022 Blizzard Entertainment, Inc.
                    """)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("About")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}
