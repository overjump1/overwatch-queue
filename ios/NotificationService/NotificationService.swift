import Foundation
import UserNotifications

/// Runs when an alert that asks for it (`mutable-content`) lands, before it shows, whether or not the
/// app is running. The worker only asks on the PC's notification speed test: this tells the worker
/// the test got here, so the PC can time it, then shows the alert as it came. Real match alerts
/// never ask, so they never wait on this.
///
/// The same file builds for the iPhone and the Watch, each as an extension of its own app.
final class NotificationService: UNNotificationServiceExtension {
    #if os(watchOS)
    private static let kind = "watch"
    #else
    private static let kind = "phone"
    #endif

    private let lock = NSLock()
    private var contentHandler: ((UNNotificationContent) -> Void)?
    private var content: UNNotificationContent?
    private var task: URLSessionDataTask?

    override func didReceive(_ request: UNNotificationRequest,
                             withContentHandler contentHandler: @escaping (UNNotificationContent) -> Void) {
        lock.withLock {
            self.contentHandler = contentHandler
            content = request.content
        }
        let info = request.content.userInfo
        guard let test = info["test"] as? String, let pair = info["pair"] as? String,
              let url = Self.arrivedURL(pair: pair, test: test)
        else { return finish() }

        var arrived = URLRequest(url: url, timeoutInterval: 5)
        arrived.httpMethod = "POST"
        arrived.setValue("application/json", forHTTPHeaderField: "Content-Type")
        arrived.httpBody = try? JSONSerialization.data(withJSONObject: ["kind": Self.kind])
        let task = URLSession.shared.dataTask(with: arrived) { [weak self] _, _, _ in self?.finish() }
        lock.withLock { self.task = task }
        task.resume()
    }

    override func serviceExtensionTimeWillExpire() {
        lock.withLock { task }?.cancel()
        finish()
    }

    /// The reply and the time running out can both get here, from different threads; the alert
    /// is handed back once.
    private func finish() {
        let (handler, content) = lock.withLock {
            defer { contentHandler = nil }
            return (contentHandler, self.content)
        }
        if let handler, let content { handler(content) }
    }

    /// Where to say so, on this app's own worker. Only IDs are taken from the push, and only
    /// well-formed ones, so a push can't point this anywhere else.
    private static func arrivedURL(pair: String, test: String) -> URL? {
        func isID(_ value: String) -> Bool {
            value.count == 32 && value.allSatisfy { "0123456789abcdef".contains($0) }
        }
        guard isID(pair), isID(test),
              let base = Bundle.main.object(forInfoDictionaryKey: "OWQWorkerURL") as? String,
              let url = URL(string: base)
        else { return nil }
        return url.appendingPathComponent("v1/pair/\(pair)/test/\(test)")
    }
}
