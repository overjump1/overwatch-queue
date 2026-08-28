import Foundation
import Observation
import UserNotifications
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
    /// Kept alive for as long as the model is, so the delegate reference `center.delegate`
    /// holds weakly doesn't get deallocated out from under it.
    private let notificationPresenter = ForegroundNotificationPresenter()

    public init() {
        store.onPhaseChange = { [weak self] _, next in
            guard let self else { return }
            switch next.kind {
            case .matchFound:
                self.matchFoundToken += 1
                // The wrist tap is the whole reason the watch app exists — this is the
                // signal that reaches you when the phone is in a pocket.
                WKInterfaceDevice.current().play(.notification)
                self.notify(for: next)
            case .mapVote, .heroSelect:
                WKInterfaceDevice.current().play(.directionUp)
                self.notify(for: next)
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
        UNUserNotificationCenter.current().delegate = notificationPresenter
        requestNotificationAuthorizationIfNeeded()

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

    /// A real, content-bearing alert that fires no matter which transport delivered the
    /// phase — the relay, or the watch's own direct connection to the PC. Unlike the
    /// mirrored Live Activity, this doesn't depend on the phone process being alive.
    private func notify(for phase: QueuePhase) {
        let content = UNMutableNotificationContent()
        content.title = NotificationCopy.title(for: phase)
        content.body = NotificationCopy.body(for: phase)
        content.sound = .default
        let request = UNNotificationRequest(identifier: UUID().uuidString,
                                            content: content,
                                            trigger: UNTimeIntervalNotificationTrigger(timeInterval: 0.1, repeats: false))
        UNUserNotificationCenter.current().add(request)
    }

    private func requestNotificationAuthorizationIfNeeded() {
        let center = UNUserNotificationCenter.current()
        center.getNotificationSettings { settings in
            guard settings.authorizationStatus == .notDetermined else { return }
            center.requestAuthorization(options: [.alert, .sound]) { _, _ in }
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

/// Without this, a local notification posted while the watch app is in the foreground is
/// delivered silently — the app is already showing the match-found/vote/pick state, but a
/// raised wrist that catches the app already open should still get the banner and sound.
private final class ForegroundNotificationPresenter: NSObject, UNUserNotificationCenterDelegate {
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        [.banner, .sound]
    }
}
