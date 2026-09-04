import SwiftUI

/// Two-tier image cache for hero portraits and map screenshots.
///
/// `AsyncImage` is deliberately not used: it routes through `URLSession.shared` (whose
/// default cache is far too small for a 53-hero roster of portraits) and it keeps no
/// decoded-image cache, so every scroll of the hero grid re-decodes from scratch.
///
/// Instead:
/// - **Memory:** decoded `UIImage`s in an `NSCache`, so re-showing a tile is instant and
///   scrolling the grid doesn't re-decode. Purged automatically under memory pressure.
/// - **Disk:** a large `URLCache` that survives launches. Catalog art URLs are
///   content-addressed (the filename is a hash), so a cached response is never stale —
///   the cache policy can safely prefer it over revalidating with the server.
@MainActor
public final class ImageCache {
    public static let shared = ImageCache()

    private let memory: NSCache<NSURL, UIImage> = {
        let cache = NSCache<NSURL, UIImage>()
        cache.countLimit = 300              // the whole roster plus every map, comfortably
        cache.totalCostLimit = 96 * 1024 * 1024
        return cache
    }()

    private let session: URLSession
    /// In-flight requests, so a grid that shows the same hero twice fetches once.
    private var inFlight: [URL: Task<UIImage?, Never>] = [:]

    private init() {
        let cache = URLCache(memoryCapacity: 8 * 1024 * 1024,
                             diskCapacity: 256 * 1024 * 1024,
                             diskPath: "overwatch-art")
        let config = URLSessionConfiguration.default
        config.urlCache = cache
        config.requestCachePolicy = .returnCacheDataElseLoad
        session = URLSession(configuration: config)
    }

    /// Synchronous memory hit, used to render without a flash of placeholder when the
    /// image has already been seen this session.
    public func cached(_ url: URL?) -> UIImage? {
        guard let url else { return nil }
        return memory.object(forKey: url as NSURL)
    }

    public func load(_ url: URL?) async -> UIImage? {
        guard let url else { return nil }
        if let hit = memory.object(forKey: url as NSURL) { return hit }

        if let existing = inFlight[url] { return await existing.value }

        let task = Task<UIImage?, Never> { [session] in
            do {
                var request = URLRequest(url: url)
                request.cachePolicy = .returnCacheDataElseLoad
                let (data, response) = try await session.data(for: request)
                guard let image = UIImage(data: data) else { return nil }

                // Art URLs are content-addressed, so once fetched they're good forever —
                // don't leave that to whatever Cache-Control the CDN happens to send.
                // Without this, a response marked no-store (or with no heuristic-caching
                // headers at all) would sail through `URLCache` uncached and get re-fetched
                // over the network on every fresh launch.
                session.configuration.urlCache?.storeCachedResponse(
                    CachedURLResponse(response: response, data: data, storagePolicy: .allowed),
                    for: request)

                return image
            } catch {
                return nil
            }
        }
        inFlight[url] = task
        let image = await task.value
        inFlight[url] = nil

        if let image {
            // Cost in bytes so the memory limit means something concrete.
            let cost = Int(image.size.width * image.size.height * image.scale * image.scale * 4)
            memory.setObject(image, forKey: url as NSURL, cost: cost)
        }
        return image
    }

    /// Bytes currently held on disk. Surfaced in the debug panel so the cache can be
    /// confirmed to be doing its job rather than taken on faith.
    public var diskUsage: Int { session.configuration.urlCache?.currentDiskUsage ?? 0 }
    public var memoryUsage: Int { session.configuration.urlCache?.currentMemoryUsage ?? 0 }

    public func clear() {
        memory.removeAllObjects()
        session.configuration.urlCache?.removeAllCachedResponses()
    }

    /// Warms the cache for art that's about to be needed — called when a hero-select or
    /// map vote opens, so tiles are already decoded by the time they animate in.
    public func prefetch(_ urls: [URL?]) {
        for url in urls.compactMap({ $0 }) where memory.object(forKey: url as NSURL) == nil {
            Task { _ = await load(url) }
        }
    }
}

/// Drop-in replacement for `AsyncImage` backed by `ImageCache`. Renders a memory hit
/// immediately — no placeholder flash when returning to a screen you've already seen.
public struct CachedImage<Placeholder: View>: View {
    private let url: URL?
    private let contentMode: ContentMode
    @ViewBuilder private let placeholder: () -> Placeholder

    @State private var image: UIImage?

    public init(url: URL?, contentMode: ContentMode = .fill,
                @ViewBuilder placeholder: @escaping () -> Placeholder) {
        self.url = url
        self.contentMode = contentMode
        self.placeholder = placeholder
        _image = State(initialValue: ImageCache.shared.cached(url))
    }

    public var body: some View {
        Group {
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .aspectRatio(contentMode: contentMode)
                    .transition(.opacity)
            } else {
                placeholder()
            }
        }
        .task(id: url) {
            guard image == nil else { return }
            let loaded = await ImageCache.shared.load(url)
            withAnimation(.easeOut(duration: 0.3)) { image = loaded }
        }
    }
}
