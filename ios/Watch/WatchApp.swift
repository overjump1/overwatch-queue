import SwiftUI
import UserNotifications
import WatchConnectivity
import WatchKit
import FirebaseCore
import FirebaseMessaging

@main
struct OverwatchQueueWatchApp: App {
    @WKApplicationDelegateAdaptor(WatchDelegate.self) private var delegate
    @StateObject private var model = WatchModel.shared
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            WatchView().environmentObject(model)
        }
        .onChange(of: scenePhase) { _, phase in
            model.setActive(phase == .active)
        }
    }
}

final class WatchDelegate: NSObject, WKApplicationDelegate, UNUserNotificationCenterDelegate, MessagingDelegate {
    func applicationDidFinishLaunching() {
        UNUserNotificationCenter.current().delegate = self
        if Bundle.main.path(forResource: "GoogleService-Info", ofType: "plist") != nil {
            FirebaseApp.configure()
            Messaging.messaging().delegate = self
        }
        Task { @MainActor in WatchModel.shared.launch() }
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in
            DispatchQueue.main.async { WKApplication.shared().registerForRemoteNotifications() }
        }
    }

    func didRegisterForRemoteNotifications(withDeviceToken deviceToken: Data) {
        guard FirebaseApp.app() != nil else { return }
        // Say which APNs environment the token is for instead of letting Firebase guess: a wrong
        // guess sends Live Activity pushes to the other environment, where Apple answers BadDeviceToken.
        #if DEBUG
        Messaging.messaging().setAPNSToken(deviceToken, type: .sandbox)
        #else
        Messaging.messaging().setAPNSToken(deviceToken, type: .prod)
        #endif
        Messaging.messaging().token { token, _ in
            guard let token else { return }
            Task { @MainActor in WatchModel.shared.setFCMToken(token) }
        }
    }

    func messaging(_ messaging: Messaging, didReceiveRegistrationToken fcmToken: String?) {
        guard let fcmToken else { return }
        Task { @MainActor in WatchModel.shared.setFCMToken(fcmToken) }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        [.banner, .sound, .list]
    }
}

/// The watch gets a "Match found!" banner and nothing else -- no Live Activity, no state push --
/// so unlike the phone its screen *is* this poll. Short is affordable because watchOS puts the
/// app away after a couple of minutes, making a session a handful of requests.
private let pollSeconds = 10.0

@MainActor
final class WatchModel: NSObject, ObservableObject, WCSessionDelegate {
    static let shared = WatchModel()

    @Published private(set) var pairID: String? = Pairing.id
    @Published private(set) var status: QueueStatus = .idle
    @Published private(set) var reachable = true

    private var fcmToken: String?
    private var active = false
    private var launched = false
    private var pollTask: Task<Void, Never>?
    private var alertedFoundAt: Double?

    func launch() {
        guard !launched, WCSession.isSupported() else { return }
        launched = true
        WCSession.default.delegate = self
        WCSession.default.activate()
    }

    func setActive(_ isActive: Bool) {
        active = isActive
        pollTask?.cancel()
        pollTask = nil
        guard isActive else { return }
        launch()
        pollTask = Task {
            while !Task.isCancelled {
                await refresh()
                try? await Task.sleep(for: .seconds(pollSeconds))
            }
        }
    }

    func setFCMToken(_ token: String) {
        fcmToken = token
        Task { await register() }
    }

    private func setPairID(_ id: String?) {
        guard id != pairID else { return }
        Pairing.id = id
        pairID = id
        status = .idle
        Task {
            await register()
            await refresh()
        }
    }

    private func register() async {
        guard let id = pairID, let fcmToken else { return }
        _ = await Worker.register(pairID: id, body: ["kind": "watch", "fcm": fcmToken])
    }

    private func refresh() async {
        guard let id = pairID else { return }
        switch await Worker.fetchStatus(pairID: id) {
        case .ok(let fetched):
            reachable = true
            if let fetched, id == pairID { apply(fetched) }
        case .reset:
            if id == pairID {
                Pairing.id = nil
                pairID = nil
                status = .idle
            }
        case .failed:
            reachable = false
        }
    }

    private func apply(_ new: QueueStatus) {
        status = new
        if new.state == .found, new.isFreshMatch, active, alertedFoundAt != new.foundAt {
            alertedFoundAt = new.foundAt
            buzz()
        }
    }

    private func buzz() {
        let device = WKInterfaceDevice.current()
        device.play(.notification)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) { device.play(.success) }
    }

    private func receive(_ context: [String: Any]) {
        if let id = context["pairID"] as? String {
            setPairID(Pairing.parse("owq://pair?id=\(id)"))
        }
    }

    nonisolated func session(_ session: WCSession, activationDidCompleteWith state: WCSessionActivationState, error: Error?) {
        let context = session.receivedApplicationContext
        Task { @MainActor in self.receive(context) }
    }

    nonisolated func session(_ session: WCSession, didReceiveApplicationContext context: [String: Any]) {
        Task { @MainActor in self.receive(context) }
    }

    nonisolated func session(_ session: WCSession, didReceiveMessage message: [String: Any]) {
        guard message["matchFound"] as? Bool == true else { return }
        Task { @MainActor in await self.refresh() }
    }
}
