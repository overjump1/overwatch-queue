import SwiftUI

/// The watch face of the app: same phases, same language, sized for a glance.
struct WatchRootView: View {
    @Environment(QueueStore.self) private var store

    var body: some View {
        ZStack {
            MeshBackground(phase: store.phase,
                           intensity: store.phase.kind == .matchFound ? 3 : 1)
                .opacity(0.85)

            content
                .transition(.blurReplace)
                .id(store.phase.kind)
        }
        .animation(.smooth(duration: 0.5), value: store.phase.kind)
    }

    @ViewBuilder
    private var content: some View {
        switch store.phase {
        case .idle:
            WatchMessageView(symbol: "moon.zzz.fill", title: "Not queued",
                             detail: "Waiting for your iPhone")
        case .searching(let info):
            WatchSearchingView(info: info)
        case .matchFound(let info):
            WatchMatchFoundView(info: info)
        case .mapVote(let info):
            WatchMapVoteView(info: info)
        case .heroSelect(let info):
            WatchHeroSelectView(info: info)
        case .inGame(let info):
            WatchMessageView(symbol: "gamecontroller.fill", title: "In game",
                             detail: store.catalog.map(info.mapKey)?.name ?? info.mode.displayName)
        case .cancelled(let info):
            WatchMessageView(symbol: "xmark.circle.fill", title: "Queue ended",
                             detail: info.displayText)
        }
    }
}

struct WatchMessageView: View {
    var symbol: String
    var title: String
    var detail: String

    var body: some View {
        VStack(spacing: 6) {
            Image(systemName: symbol)
                .font(.title2)
                .foregroundStyle(Palette.orange)
            Text(title).font(.headline).foregroundStyle(Palette.white)
            Text(detail)
                .font(.caption2)
                .foregroundStyle(Palette.white.opacity(0.6))
                .multilineTextAlignment(.center)
        }
        .padding()
    }
}

struct WatchSearchingView: View {
    var info: SearchInfo
    @Environment(QueueStore.self) private var store

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let start = store.clock.toLocal(info.startedAt)
            let elapsed = max(0, context.date.timeIntervalSince(start))
            let progress = info.estimatedWait.map { min(1, elapsed / max(1, $0)) }
            let overdue = info.estimatedWait.map { elapsed > $0 } ?? false

            VStack(spacing: 4) {
                ZStack {
                    // No particle field here: the orb is small enough that it would read
                    // as noise, and the watch's frame budget is better spent elsewhere.
                    SearchOrb(tint: info.role.tint,
                              symbolName: info.role.symbolName,
                              progress: progress,
                              isOverdue: overdue,
                              showsParticles: false)
                }
                .frame(width: 96, height: 96)

                ElapsedTimer(since: start, size: 30, weight: .semibold)

                Text(info.role.displayName.uppercased())
                    .font(.system(size: 10, weight: .heavy))
                    .tracking(1.4)
                    .foregroundStyle(info.role.tint)

                WaitMeter(progress: progress, isOverdue: overdue, tint: info.role.tint)
                    .padding(.horizontal, 22)
            }
        }
    }
}

struct WatchMatchFoundView: View {
    var info: MatchFoundInfo
    @Environment(QueueStore.self) private var store
    @State private var pulse = false

    var body: some View {
        let deadline = store.clock.toLocal(info.lockInAt)

        VStack(spacing: 8) {
            Image(systemName: "bolt.fill")
                .font(.system(size: 30, weight: .bold))
                .foregroundStyle(Palette.orange)
                .scaleEffect(pulse ? 1.15 : 0.9)
                .animation(.easeInOut(duration: 0.5).repeatForever(autoreverses: true), value: pulse)

            Text("MATCH FOUND")
                .font(.system(size: 15, weight: .black))
                .foregroundStyle(Palette.white)

            Text.countdown(to: deadline)
                .font(.system(size: 24, weight: .bold, design: .rounded))
                .monospacedDigit()
                .foregroundStyle(Palette.amber)

            Text("waited \(QueueTime.compact(info.waited))")
                .font(.system(size: 10))
                .foregroundStyle(Palette.white.opacity(0.55))
        }
        .onAppear { pulse = true }
    }
}

struct WatchMapVoteView: View {
    var info: MapVoteInfo
    @Environment(QueueStore.self) private var store

    var body: some View {
        List {
            Section {
                ForEach(info.options) { option in
                    Button {
                        store.vote(map: option.mapKey)
                    } label: {
                        HStack {
                            VStack(alignment: .leading, spacing: 1) {
                                Text(store.catalog.map(option.mapKey)?.name
                                     ?? option.mapKey.capitalized)
                                    .font(.caption.weight(.semibold))
                                if let type = store.catalog.map(option.mapKey)?.primaryType {
                                    Text(type.displayName)
                                        .font(.system(size: 9))
                                        .foregroundStyle(.secondary)
                                }
                            }
                            Spacer()
                            if info.myVote == option.mapKey {
                                Image(systemName: "checkmark.circle.fill")
                                    .foregroundStyle(Palette.amber)
                            } else {
                                Text("\(option.votes)")
                                    .font(.caption2.monospacedDigit())
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            } header: {
                Text("Map vote")
            }
        }
        .sensoryFeedback(.selection, trigger: info.myVote)
    }
}

struct WatchHeroSelectView: View {
    var info: HeroSelectInfo
    @Environment(QueueStore.self) private var store

    private let columns = [GridItem(.adaptive(minimum: 48), spacing: 6)]

    var body: some View {
        ScrollView {
            LazyVGrid(columns: columns, spacing: 6) {
                ForEach(store.selectableHeroes()) { hero in
                    Button {
                        store.select(hero: hero.key)
                    } label: {
                        HeroPortrait(hero: hero, size: 48)
                            .overlay {
                                RoundedRectangle(cornerRadius: 11, style: .continuous)
                                    .strokeBorder(info.myHeroKey == hero.key
                                                  ? Palette.amber : .clear,
                                                  lineWidth: 2.5)
                            }
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 4)
        }
        .sensoryFeedback(.selection, trigger: info.myHeroKey)
    }
}
