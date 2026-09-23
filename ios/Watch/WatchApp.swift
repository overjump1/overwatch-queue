import SwiftUI
import UserNotifications
import WatchConnectivity
import WatchKit
import FirebaseCore
import FirebaseMessaging

@main
struct OverQueueWatchApp: App {
    @WKApplicationDelegateAdaptor(WatchDelegate.self) private var delegate
    @StateObject private var model = WatchModel.shared
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            WatchView().environmentObject(model)
        }
        .onChange(of: scenePhase) { _, phase in
            // .inactive is the wrist going down with the app still up; pushes still land then, so
            // only coming back from the background is worth asking the worker.
            guard phase != .inactive else { return }
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

    /// The worker's silent push with the state in it, sent on every change.
    func didReceiveRemoteNotification(_ userInfo: [AnyHashable: Any]) async -> WKBackgroundFetchResult {
        guard let pushed = userInfo["status"],
              let data = try? JSONSerialization.data(withJSONObject: pushed),
              let status = try? JSONDecoder().decode(QueueStatus.self, from: data)
        else { return .noData }
        await WatchModel.shared.applyPushed(status)
        return .newData
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

@MainActor
final class WatchModel: NSObject, ObservableObject, WCSessionDelegate {
    static let shared = WatchModel()

    @Published private(set) var pairID: String? = Pairing.id
    @Published private(set) var status: QueueStatus = .idle
    @Published private(set) var reachable = true

    private var fcmToken: String?
    private var active = false
    private var launched = false
    private var alertedFoundAt: Double?

    func launch() {
        guard !launched, WCSession.isSupported() else { return }
        launched = true
        WCSession.default.delegate = self
        WCSession.default.activate()
    }

    /// No poll: the worker pushes every change here (`applyPushed`). The one request is on the way in,
    /// since watchOS holds back silent pushes to an app that isn't open and some may not have landed.
    func setActive(_ isActive: Bool) {
        active = isActive
        guard isActive else { return }
        launch()
        Task { await refresh() }
    }

    /// A state push from the worker.
    func applyPushed(_ pushed: QueueStatus) {
        guard pairID != nil else { return }
        apply(pushed)
    }

    func setFCMToken(_ token: String) {
        // Launch hands over the same token twice: once asked for, once from the delegate.
        guard token != fcmToken else { return }
        fcmToken = token
        Task { await register() }
    }

    private func setPairID(_ id: String?) {
        guard id != pairID else { return }
        Pairing.id = id
        pairID = id
        status = .idle
        Task {
            // The register's reply carries the state; only with no token to register is there
            // nothing to bring it back.
            if fcmToken != nil { await register() } else { await refresh() }
        }
    }

    private func register() async {
        guard let id = pairID, let fcmToken else { return }
        handle(await Worker.register(pairID: id, body: ["kind": "watch", "fcm": fcmToken]), for: id)
    }

    private func refresh() async {
        guard let id = pairID else { return }
        handle(await Worker.fetchStatus(pairID: id), for: id)
    }

    private func handle(_ result: Worker.Result, for id: String) {
        switch result {
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
            // The phone already checked the link; the Watch has none of its own to check it against.
            setPairID(Pairing.validID(id))
        }
    }

    nonisolated func session(_ session: WCSession, activationDidCompleteWith state: WCSessionActivationState, error: Error?) {
        let context = session.receivedApplicationContext
        Task { @MainActor in self.receive(context) }
    }

    nonisolated func session(_ session: WCSession, didReceiveApplicationContext context: [String: Any]) {
        Task { @MainActor in self.receive(context) }
    }
}
