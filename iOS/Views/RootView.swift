import SwiftUI

/// Switches between phase screens over the shared animated background. Only the content
/// layer transitions — the background persists and re-tints, so moving between phases
/// feels like one continuous space rather than a slideshow.
struct RootView: View {
    @Environment(AppModel.self) private var model
    @Environment(QueueStore.self) private var store
    @State private var showSettings = false

    var body: some View {
        if model.isPaired {
            queue
        } else {
            // Nothing to show until a PC is paired: it is the only source of state.
            PairingView()
                .transition(.opacity)
        }
    }

    private var queue: some View {
        ZStack {
            MeshBackground(phase: store.phase,
                           intensity: store.phase.kind == .matchFound ? 3.5 : 1)

            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .transition(.blurReplace.combined(with: .scale(0.94)))
                .id(store.phase.kind)

            MatchFoundBurst(token: model.matchFoundToken)
                .allowsHitTesting(false)
        }
        .animation(.smooth(duration: 0.55), value: store.phase.kind)
        .overlay(alignment: .top) { StatusBar(showSettings: $showSettings) }
        .sheet(isPresented: $showSettings) {
            SettingsView().environment(model)
        }
    }

    @ViewBuilder
    private var content: some View {
        switch store.phase {
        case .idle:
            IdleView()
        case .searching(let info):
            SearchingView(info: info)
        case .matchFound(let info):
            MatchFoundView(info: info)
        case .mapVote(let info):
            MapVoteView(info: info)
        case .heroSelect(let info):
            HeroSelectView(info: info)
        case .inGame(let info):
            InGameView(info: info)
        case .cancelled(let info):
            CancelledView(info: info)
        }
    }
}

/// A thin always-present header: whether the PC is talking, and the way into settings.
private struct StatusBar: View {
    @Environment(AppModel.self) private var model
    @Environment(QueueStore.self) private var store
    @Binding var showSettings: Bool

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: store.status.symbolName)
                .foregroundStyle(store.status.isLive ? Palette.support : Palette.neutral)
                .font(.caption)

            Text(store.status.displayText)
                .font(.caption.weight(.medium))
                .foregroundStyle(Palette.white.opacity(0.65))

            Spacer()

            Button {
                showSettings = true
            } label: {
                Image(systemName: "slider.horizontal.3")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(Palette.white.opacity(0.8))
                    .padding(8)
                    .background(.ultraThinMaterial, in: Circle())
            }
        }
        .padding(.horizontal, 20)
        .padding(.top, 4)
    }
}

// MARK: - Idle

struct IdleView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(spacing: 20) {
            Image(systemName: "moon.zzz.fill")
                .font(.system(size: 56, weight: .light))
                .foregroundStyle(Palette.neutral)
                .symbolEffect(.pulse, options: .repeating)

            Text("Not in queue")
                .font(.title2.weight(.semibold))
                .foregroundStyle(Palette.white)

            Text("Start a queue on your PC and it'll show up here.")
                .font(.subheadline)
                .foregroundStyle(Palette.white.opacity(0.55))
                .multilineTextAlignment(.center)
                .padding(.horizontal, 48)

            if let pairing = model.pairing {
                Label(pairing.displayText, systemImage: "desktopcomputer")
                    .font(.footnote)
                    .foregroundStyle(Palette.white.opacity(0.35))
                    .padding(.top, 6)
            }
        }
    }
}

// MARK: - Searching

struct SearchingView: View {
    var info: SearchInfo
    @Environment(QueueStore.self) private var store

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let now = context.date
            let start = store.clock.toLocal(info.startedAt)
            let elapsed = max(0, now.timeIntervalSince(start))
            let progress = info.estimatedWait.map { min(1, elapsed / max(1, $0)) }
            let overdue = info.estimatedWait.map { elapsed > $0 } ?? false

            VStack(spacing: 0) {
                Spacer()

                SearchOrb(tint: info.role.tint,
                          symbolName: info.role.symbolName,
                          progress: progress,
                          isOverdue: overdue)
                    .frame(width: 260, height: 260)
                    .overlay(alignment: .bottom) {
                        ElapsedTimer(since: start)
                            .offset(y: 54)
                    }

                Spacer().frame(height: 78)

                VStack(spacing: 6) {
                    Text(info.mode.displayName.uppercased())
                        .font(.caption.weight(.heavy))
                        .tracking(2.4)
                        .foregroundStyle(Palette.white.opacity(0.55))

                    HStack(spacing: 8) {
                        Image(systemName: info.role.symbolName)
                        Text(info.role.displayName)
                        if info.groupSize > 1 {
                            Text("· \(info.groupSize)-stack")
                                .foregroundStyle(Palette.white.opacity(0.5))
                        }
                    }
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(info.role.tint)
                }

                VStack(spacing: 10) {
                    WaitMeter(progress: progress, isOverdue: overdue, tint: info.role.tint)
                        .padding(.horizontal, 44)

                    Text(overdue
                         ? "Longer than usual"
                         : "Estimated \(QueueTime.estimate(info.estimatedWait))")
                        .font(.footnote)
                        .foregroundStyle(overdue ? Palette.amber : Palette.white.opacity(0.5))
                        .contentTransition(.opacity)
                }
                .padding(.top, 30)

                Spacer()

                Button(role: .destructive) {
                    store.cancelQueue()
                } label: {
                    Text("Leave Queue")
                        .font(.subheadline.weight(.semibold))
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .tint(Palette.neutral)
                .padding(.horizontal, 44)
                .padding(.bottom, 34)
            }
        }
    }
}

// MARK: - In game / cancelled

struct InGameView: View {
    var info: InGameInfo
    @Environment(QueueStore.self) private var store

    var body: some View {
        VStack(spacing: 18) {
            Spacer()

            if let map = store.catalog.map(info.mapKey) {
                MapImageView(map: map)
                    .frame(height: 190)
                    .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
                    .overlay(alignment: .bottomLeading) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(map.name).font(.title3.weight(.bold))
                            Text(map.primaryType.displayName)
                                .font(.caption).foregroundStyle(Palette.white.opacity(0.6))
                        }
                        .foregroundStyle(Palette.white)
                        .padding(16)
                    }
                    .padding(.horizontal, 24)
            }

            if let hero = store.catalog.hero(info.heroKey) {
                HStack(spacing: 12) {
                    HeroPortrait(hero: hero, size: 52)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(hero.name).font(.headline)
                        Text(hero.role.displayName)
                            .font(.caption).foregroundStyle(hero.role.tint)
                    }
                    Spacer()
                }
                .padding(.horizontal, 28)
            }

            VStack(spacing: 4) {
                Text("IN GAME")
                    .font(.caption.weight(.heavy)).tracking(2.4)
                    .foregroundStyle(Palette.support)
                ElapsedTimer(since: store.clock.toLocal(info.startedAt), size: 38, weight: .semibold)
            }
            .padding(.top, 6)

            Spacer()
        }
    }
}

struct CancelledView: View {
    var info: CancelInfo

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "xmark.circle.fill")
                .font(.system(size: 48))
                .foregroundStyle(Palette.neutral)
            Text(info.displayText)
                .font(.title3.weight(.medium))
                .foregroundStyle(Palette.white.opacity(0.8))
                .multilineTextAlignment(.center)
                .padding(.horizontal, 40)
        }
    }
}
