import SwiftUI

/// Map vote: cards flip in one after another, and picking one ignites its border and
/// fills its tally. The countdown lives at the top so the deadline is never off-screen
/// while you're deciding.
struct MapVoteView: View {
    var info: MapVoteInfo
    @Environment(QueueStore.self) private var store
    @State private var revealed = false

    private var deadline: Date { store.clock.toLocal(info.deadline) }

    var body: some View {
        VStack(spacing: 16) {
            header

            ScrollView {
                VStack(spacing: 14) {
                    ForEach(Array(info.options.enumerated()), id: \.element.id) { index, option in
                        MapVoteCard(option: option,
                                    map: store.catalog.map(option.mapKey),
                                    share: info.share(of: option),
                                    isMyVote: info.myVote == option.mapKey,
                                    isLeading: info.leader?.mapKey == option.mapKey && info.totalVotes > 0)
                            .onTapGesture {
                                withAnimation(.bouncy(duration: 0.45)) {
                                    store.vote(map: option.mapKey)
                                }
                            }
                            // Staggered flip-in: each card arrives on its own beat, which
                            // reads as a hand being dealt rather than a list appearing.
                            .rotation3DEffect(.degrees(revealed ? 0 : 72),
                                              axis: (x: 1, y: 0, z: 0),
                                              anchor: .top, perspective: 0.55)
                            .opacity(revealed ? 1 : 0)
                            .animation(.bouncy(duration: 0.65, extraBounce: 0.15)
                                .delay(Double(index) * 0.09), value: revealed)
                    }
                }
                .padding(.horizontal, 20)
                .padding(.bottom, 28)
            }
            .scrollIndicators(.hidden)
        }
        .padding(.top, 44)
        .onAppear { revealed = true }
        .sensoryFeedback(.selection, trigger: info.myVote)
    }

    private var header: some View {
        HStack(spacing: 14) {
            CountdownRing(deadline: deadline, total: 25, lineWidth: 3, tint: Palette.amber)
                .frame(width: 40, height: 40)
                .overlay {
                    Text.countdown(to: deadline)
                        .font(.system(size: 11, weight: .bold, design: .rounded))
                        .monospacedDigit()
                        .foregroundStyle(Palette.white)
                }

            VStack(alignment: .leading, spacing: 2) {
                Text("VOTE FOR A MAP")
                    .font(.caption.weight(.heavy)).tracking(2)
                    .foregroundStyle(Palette.amber)
                Text(info.myVote == nil
                     ? "Tap the one you want"
                     : "Vote locked — you can change it")
                    .font(.caption2)
                    .foregroundStyle(Palette.white.opacity(0.55))
            }
            Spacer()
        }
        .padding(.horizontal, 24)
    }
}

private struct MapVoteCard: View {
    var option: MapOption
    var map: OverwatchMap?
    var share: Double
    var isMyVote: Bool
    var isLeading: Bool

    var body: some View {
        // The image is clamped to the card height *before* the label is overlaid.
        // Overlaying inside a ZStack instead lets the full-resolution image drive the
        // stack's height, which pushes the label below the visible crop.
        Group {
            if let map {
                MapImageView(map: map)
            } else {
                Palette.deepBlue
            }
        }
        .frame(height: 156)
        .clipped()
        .overlay(alignment: .bottomLeading) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(map?.name ?? option.mapKey.capitalized)
                        .font(.title3.weight(.bold))
                        .foregroundStyle(Palette.white)
                    if let flag = map?.flag {
                        Text(flag).font(.footnote)
                    }
                    Spacer()
                    if isMyVote {
                        Label("Your vote", systemImage: "checkmark.seal.fill")
                            .font(.caption2.weight(.bold))
                            .foregroundStyle(Palette.amber)
                            .labelStyle(.titleAndIcon)
                    }
                }

                HStack(spacing: 8) {
                    if let map {
                        Label(map.primaryType.displayName, systemImage: map.primaryType.symbolName)
                            .font(.caption2.weight(.medium))
                            .foregroundStyle(Palette.white.opacity(0.7))
                    }
                    Spacer()
                    Text("\(option.votes)")
                        .font(.caption.weight(.bold).monospacedDigit())
                        .foregroundStyle(Palette.white.opacity(0.9))
                        .contentTransition(.numericText())
                }

                // Tally bar — how the vote is trending, without a separate results screen.
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        Capsule().fill(Palette.white.opacity(0.15))
                        Capsule()
                            .fill(isLeading ? Palette.amber : Palette.white.opacity(0.55))
                            .frame(width: max(3, geo.size.width * share))
                            .animation(.smooth(duration: 0.5), value: share)
                    }
                }
                .frame(height: 4)
            }
            .padding(16)
        }
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .strokeBorder(
                    isMyVote
                        ? AnyShapeStyle(LinearGradient(colors: [Palette.amber, Palette.orange],
                                                       startPoint: .topLeading, endPoint: .bottomTrailing))
                        : AnyShapeStyle(Palette.white.opacity(0.10)),
                    lineWidth: isMyVote ? 2.5 : 1)
        }
        .shadow(color: isMyVote ? Palette.orange.opacity(0.45) : .black.opacity(0.3),
                radius: isMyVote ? 16 : 8, y: 4)
        .scaleEffect(isMyVote ? 1.02 : 1)
    }
}
