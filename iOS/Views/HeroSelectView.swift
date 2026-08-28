import SwiftUI

/// Hero select: the roster for your role, filtered to what's actually available and
/// minus anyone your team already locked. Selecting lifts the tile and desaturates the
/// rest, so the choice is legible at a glance from across a desk.
struct HeroSelectView: View {
    var info: HeroSelectInfo
    @Environment(QueueStore.self) private var store
    @State private var appeared = false

    private var deadline: Date { store.clock.toLocal(info.deadline) }

    private var heroes: [Hero] {
        store.selectableHeroes()
    }

    private let columns = [GridItem(.adaptive(minimum: 84), spacing: 12)]

    var body: some View {
        VStack(spacing: 14) {
            header

            if heroes.isEmpty {
                ContentUnavailableView("Loading heroes…",
                                       systemImage: "person.crop.square.fill",
                                       description: Text("Fetching the roster from the catalog."))
                    .foregroundStyle(Palette.white.opacity(0.6))
            } else {
                ScrollView {
                    LazyVGrid(columns: columns, spacing: 12) {
                        ForEach(Array(heroes.enumerated()), id: \.element.id) { index, hero in
                            HeroTile(hero: hero, isSelected: info.myHeroKey == hero.key,
                                     dimmed: info.myHeroKey != nil && info.myHeroKey != hero.key)
                                .onTapGesture {
                                    withAnimation(.bouncy(duration: 0.4)) {
                                        store.select(hero: hero.key)
                                    }
                                }
                                .opacity(appeared ? 1 : 0)
                                .scaleEffect(appeared ? 1 : 0.7)
                                .animation(.bouncy(duration: 0.5)
                                    .delay(Double(min(index, 18)) * 0.022), value: appeared)
                        }
                    }
                    .padding(.horizontal, 18)
                    .padding(.bottom, 30)
                }
                .scrollIndicators(.hidden)
            }
        }
        .padding(.top, 44)
        .onAppear { appeared = true }
        .sensoryFeedback(.selection, trigger: info.myHeroKey)
    }

    private var header: some View {
        HStack(spacing: 14) {
            CountdownRing(deadline: deadline, total: 40, lineWidth: 3, tint: info.role.tint)
                .frame(width: 40, height: 40)
                .overlay {
                    Text.countdown(to: deadline)
                        .font(.system(size: 11, weight: .bold, design: .rounded))
                        .monospacedDigit()
                        .foregroundStyle(Palette.white)
                }

            VStack(alignment: .leading, spacing: 2) {
                Text("CHOOSE YOUR HERO")
                    .font(.caption.weight(.heavy)).tracking(2)
                    .foregroundStyle(info.role.tint)
                HStack(spacing: 4) {
                    Image(systemName: info.role.symbolName).font(.caption2)
                    Text(info.role.displayName)
                    if let map = store.catalog.map(info.mapKey) {
                        Text("· \(map.name)")
                    }
                }
                .font(.caption2)
                .foregroundStyle(Palette.white.opacity(0.55))
            }
            Spacer()
        }
        .padding(.horizontal, 24)
    }
}

private struct HeroTile: View {
    var hero: Hero
    var isSelected: Bool
    var dimmed: Bool

    var body: some View {
        VStack(spacing: 5) {
            HeroPortrait(hero: hero, size: 84)
                .overlay {
                    RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .strokeBorder(
                            isSelected
                                ? AnyShapeStyle(LinearGradient(colors: [Palette.amber, Palette.orange],
                                                               startPoint: .top, endPoint: .bottom))
                                : AnyShapeStyle(Palette.white.opacity(0.08)),
                            lineWidth: isSelected ? 3 : 1)
                }
                .shadow(color: isSelected ? Palette.orange.opacity(0.6) : .clear,
                        radius: 14, y: 4)

            Text(hero.name)
                .font(.caption2.weight(isSelected ? .bold : .medium))
                .foregroundStyle(isSelected ? Palette.white : Palette.white.opacity(0.65))
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
        .grayscale(dimmed ? 0.85 : 0)
        .opacity(dimmed ? 0.45 : 1)
        .scaleEffect(isSelected ? 1.08 : 1)
    }
}
