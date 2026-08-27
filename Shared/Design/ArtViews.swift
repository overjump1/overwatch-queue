import SwiftUI

/// Loads hero and map art. Order of preference:
/// 1. An image in the asset catalog named `Hero/<key>` or `Map/<key>` — drop your own
///    files there to override any individual entry.
/// 2. The catalog's URL (Blizzard's CDN for portraits), loaded through `ImageCache`,
///    which keeps them decoded in memory and on disk across launches.
/// 3. A drawn placeholder, so a slow network or a missing entry never leaves a hole.
public struct RemoteArt<Placeholder: View>: View {
    public var url: URL?
    public var localName: String?
    public var contentMode: ContentMode
    @ViewBuilder public var placeholder: () -> Placeholder

    public init(url: URL?, localName: String? = nil, contentMode: ContentMode = .fill,
                @ViewBuilder placeholder: @escaping () -> Placeholder) {
        self.url = url
        self.localName = localName
        self.contentMode = contentMode
        self.placeholder = placeholder
    }

    private var localImage: UIImage? {
        guard let localName else { return nil }
        return UIImage(named: localName)
    }

    public var body: some View {
        if let localImage {
            Image(uiImage: localImage)
                .resizable()
                .aspectRatio(contentMode: contentMode)
        } else if url != nil {
            CachedImage(url: url, contentMode: contentMode, placeholder: placeholder)
        } else {
            placeholder()
        }
    }
}

/// A hero's portrait, with a role-tinted initials tile behind it.
public struct HeroPortrait: View {
    public var hero: Hero
    public var size: CGFloat

    public init(hero: Hero, size: CGFloat = 72) {
        self.hero = hero
        self.size = size
    }

    public var body: some View {
        RemoteArt(url: hero.portrait, localName: "Hero/\(hero.key)") {
            ZStack {
                LinearGradient(colors: [hero.role.tint.opacity(0.55), Palette.deepBlue],
                               startPoint: .top, endPoint: .bottom)
                Text(hero.initials)
                    .font(.system(size: size * 0.32, weight: .heavy, design: .rounded))
                    .foregroundStyle(Palette.white.opacity(0.85))
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: size * 0.22, style: .continuous))
    }
}

/// A map screenshot with a legibility scrim, so the name on top stays readable over
/// whatever the image happens to be.
public struct MapImageView: View {
    public var map: OverwatchMap

    public init(map: OverwatchMap) {
        self.map = map
    }

    public var body: some View {
        RemoteArt(url: map.screenshot, localName: "Map/\(map.key)") {
            ZStack {
                LinearGradient(colors: [Palette.blue.opacity(0.5), Palette.night],
                               startPoint: .topLeading, endPoint: .bottomTrailing)
                Image(systemName: map.primaryType.symbolName)
                    .font(.system(size: 34, weight: .light))
                    .foregroundStyle(Palette.white.opacity(0.28))
            }
        }
        // Map art is unpredictable — Havana and Nepal are bright skies where Anubis is
        // dark interior — so the scrim has to be strong enough for white text on the
        // brightest of them.
        .overlay(alignment: .bottom) {
            LinearGradient(stops: [
                .init(color: .clear, location: 0),
                .init(color: Palette.night.opacity(0.55), location: 0.55),
                .init(color: Palette.night.opacity(0.95), location: 1),
            ], startPoint: .top, endPoint: .bottom)
        }
    }
}
