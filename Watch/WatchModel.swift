import Foundation
import Observation
import WatchKit

/// Watch-side counterpart to `AppModel`. State arrives from the paired iPhone; votes and
/// hero picks go back the same way.
@MainActor
@Observable
public final class WatchModel {
    public let store = QueueStore()
    public let catalog = CatalogService.shared
    public private(set) var matchFoundToken = 0

    /// The phone while it's in range, the PC when it isn't — see `WatchTransport`.
    private var link: WatchTransport?

    public init() {
        store.onPhaseChange = { [weak self] _, next in
            guard let self else { return }
            switch next.kind {
            case .matchFound:
                self.matchFoundToken += 1
                // The wrist tap is the whole reason the watch app exists — this is the
                // signal that reaches you when the phone is in a pocket. No notification
                // banner alongside it — the Live Activity on the phone is the only thing
                // that surfaces this beyond a glance at the watch itself.
                WKInterfaceDevice.current().play(.notification)
            case .mapVote, .heroSelect:
                WKInterfaceDevice.current().play(.directionUp)
                // Same reasoning as on the phone, and more acute here: the watch has a
                // slower link, so art must be in hand before the grid appears.
                switch next {
                case .mapVote(let info):
                    ImageCache.shared.prefetch(info.options.map { self.catalog.map($0.mapKey)?.screenshot })
                case .heroSelect:
                    ImageCache.shared.prefetch(self.store.selectableHeroes().map(\.portrait))
                default:
                    break
                }
            default:
                break
            }
        }
        store.onSnapshot = { snapshot in
            SharedState.write(snapshot)
        }
    }

    public func start() {
        WKApplication.shared().registerForRemoteNotifications()

        #if DEBUG
        // Standalone mode: `-server <host:port> -token <uuid>` points the watch straight
        // at the PC instead of at the paired iPhone. WatchConnectivity between paired
        // *simulators* is unreliable, so without this the watch UI would only be testable
        // on hardware.
        if let pairing = Self.launchPairing() {
            store.use(WebSocketTransport(pairing: pairing, identity: identity))
            Task { await catalog.load() }
            return
        }
        #endif

        // Nothing is ever typed on the wrist: the pairing arrives from the phone, which
        // got it from the one code you scanned.
        let link = WatchTransport(pairing: Pairing.load(), identity: identity)
        self.link = link
        WatchLink.shared.onPairing = { [weak self] pairing in
            // Kept, so a relaunch out of range still knows where the PC is.
            if let pairing { pairing.save() } else { Pairing.forget() }
            self?.link?.adopt(pairing)
        }
        store.use(link)
        Task { await catalog.load() }
    }

    // MARK: - Push notifications
    //
    // Independent of the phone relay on purpose: this is the token APNs uses to wake the
    // watch directly, for the case the relay is built to fail over from — the phone is
    // out of range, asleep, or simply not carried.

    public func didReceive(deviceToken: Data) {
        store.registerPushToken(deviceToken.hexEncoded, environment: .current)
    }

    /// A background push arrived: make sure a connection is live and ask for the truth.
    public func handleBackgroundPush(completion: @escaping () -> Void) {
        if store.transport?.status.isLive != true { store.transport?.connect() }
        store.requestRefresh()
        Task {
            try? await Task.sleep(for: .seconds(3))
            completion()
        }
    }

    private var identity: ClientIdentity {
        ClientIdentity(kind: .watch,
                       name: WKInterfaceDevice.current().name,
                       appVersion: Bundle.main.appVersion)
    }

    #if DEBUG
    private static func launchPairing() -> Pairing? {
        let arguments = ProcessInfo.processInfo.arguments
        func value(_ flag: String) -> String? {
            guard let index = arguments.firstIndex(of: flag), arguments.count > index + 1
            else { return nil }
            return arguments[index + 1]
        }
        guard let server = value("-server"), let token = value("-token") else { return nil }
        let parts = server.split(separator: ":")
        return Pairing(host: String(parts[0]),
                       port: parts.count > 1 ? Int(parts[1]) ?? Pairing.defaultPort
                                             : Pairing.defaultPort,
                       token: token)
    }
    #endif
}
