import SwiftUI

/// The watch face of the app: same phases, same language, sized for a glance.
///
/// Being a plain switch over the phase is what makes every way in land somewhere sensible
/// without any routing: a notification tap, the Smart Stack's mirrored Live Activity, the
/// accessory complication and a cold launch all arrive here and get the screen for
/// whatever is happening right now. There is deliberately no navigation state and no deep
/// link target — the queue's phase *is* the destination, and anything that tried to
/// remember a different one could only ever be out of date.
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

    private var deadline: Date { store.clock.toLocal(info.deadline) }

    var body: some View {
        List {
            Section {
                ForEach(info.options) { option in
                    Button {
                        store.vote(map: option.mapKey)
                    } label: {
                        WatchMapVoteRow(option: option,
                                         map: store.catalog.map(option.mapKey),
                                         isMyVote: info.myVote == option.mapKey)
                    }
                    .buttonStyle(.plain)
                    .listRowInsets(EdgeInsets(top: 4, leading: 4, bottom: 4, trailing: 4))
                }
            } header: {
                HStack(spacing: 6) {
                    Text("Map vote")
                    Spacer()
                    CountdownRing(deadline: deadline, total: 25, lineWidth: 2, tint: Palette.amber)
                        .frame(width: 16, height: 16)
                    Text.countdown(to: deadline)
                        .font(.system(size: 11, weight: .bold, design: .rounded))
                        .monospacedDigit()
                        .foregroundStyle(Palette.white.opacity(0.8))
                }
            }
        }
        .sensoryFeedback(.selection, trigger: info.myVote)
    }
}

private struct WatchMapVoteRow: View {
    var option: MapOption
    var map: OverwatchMap?
    var isMyVote: Bool

    var body: some View {
        HStack(spacing: 8) {
            Group {
                if let map {
                    MapImageView(map: map)
                } else {
                    Palette.deepBlue
                }
            }
            .frame(width: 44, height: 44)
            .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))

            VStack(alignment: .leading, spacing: 2) {
                Text(map?.name ?? option.mapKey.capitalized)
                    .font(.caption.weight(.semibold))
                    .lineLimit(1)
                if let type = map?.primaryType {
                    Label(type.displayName, systemImage: type.symbolName)
                        .font(.system(size: 9))
                        .foregroundStyle(.secondary)
                        .labelStyle(.titleAndIcon)
                }
            }

            Spacer(minLength: 4)

            VStack(spacing: 2) {
                if isMyVote {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(Palette.amber)
                }
                Text("\(option.votes)")
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
        }
        .overlay {
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .strokeBorder(isMyVote ? Palette.amber : .clear, lineWidth: 1.5)
        }
    }
}

struct WatchHeroSelectView: View {
    var info: HeroSelectInfo
    @Environment(QueueStore.self) private var store

    private let columns = [GridItem(.adaptive(minimum: 48), spacing: 6)]

    private var deadline: Date { store.clock.toLocal(info.deadline) }

    var body: some View {
        ScrollView {
            HStack(spacing: 6) {
                CountdownRing(deadline: deadline, total: 40, lineWidth: 2, tint: info.role.tint)
                    .frame(width: 18, height: 18)
                Text.countdown(to: deadline)
                    .font(.system(size: 13, weight: .bold, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(Palette.white)
                Spacer(minLength: 0)
                Image(systemName: info.role.symbolName)
                    .font(.system(size: 10))
                    .foregroundStyle(info.role.tint)
            }
            .padding(.horizontal, 4)
            .padding(.bottom, 4)

            let picks = store.teamPicks()
            if !picks.isEmpty {
                // Small enough to read without scrolling past it, so the composition is
                // the first thing on the wrist rather than something to hunt for.
                HStack(spacing: 4) {
                    ForEach(picks, id: \.pick.slot) { entry in
                        HeroPortrait(hero: entry.hero, size: 26)
                            .overlay {
                                RoundedRectangle(cornerRadius: 6, style: .continuous)
                                    .strokeBorder(entry.pick.isSelf
                                                  ? AnyShapeStyle(Palette.amber)
                                                  : AnyShapeStyle(entry.hero.role.tint.opacity(0.6)),
                                                  lineWidth: entry.pick.isSelf ? 2 : 1)
                            }
                            .accessibilityLabel(entry.pick.isSelf
                                                ? "You, \(entry.hero.name)"
                                                : entry.hero.name)
                    }
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, 4)
                .padding(.bottom, 4)
            }

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
