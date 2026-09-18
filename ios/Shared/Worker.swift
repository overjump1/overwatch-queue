import Foundation

/// The Cloudflare worker: the only thing the apps talk to.
enum Worker {
    static var base: URL? {
        #if DEBUG
        if let override = UserDefaults.standard.string(forKey: "workerURL") { return URL(string: override) }
        #endif
        return URL(string: "https://overwatch-queue-push-relay.tomerady.workers.dev")
    }

    enum Result {
        case ok(QueueStatus?)
        /// The PC made a new pairing code; this one no longer works.
        case reset
        case failed
    }

    private struct StateResponse: Decodable {
        let status: QueueStatus
    }

    static func fetchStatus(pairID: String) async -> Result {
        await send("GET", path: "/v1/pair/\(pairID)/state", body: nil) { data in
            try? JSONDecoder().decode(StateResponse.self, from: data).status
        }
    }

    static func register(pairID: String, body: [String: Any]) async -> Result {
        await send("POST", path: "/v1/pair/\(pairID)/device", body: body) { _ in nil }
    }

    private static func send(_ method: String, path: String, body: [String: Any]?,
                             decode: (Data) -> QueueStatus?) async -> Result {
        guard let url = base?.appendingPathComponent(path) else { return .failed }
        var request = URLRequest(url: url, timeoutInterval: 10)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let body {
            request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        }
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            let code = (response as? HTTPURLResponse)?.statusCode ?? 0
            if code == 410 { return .reset }
            guard code == 200 else { return .failed }
            return .ok(decode(data))
        } catch {
            return .failed
        }
    }
}

enum Pairing {
    private static let key = "pairID"

    static var id: String? {
        get { UserDefaults.standard.string(forKey: key) }
        set { UserDefaults.standard.set(newValue, forKey: key) }
    }

    /// Accepts `owq://pair?id=<32 hex>` (from the QR code) and returns the pairing id.
    static func parse(_ text: String) -> String? {
        guard let components = URLComponents(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
              components.scheme == "owq", components.host == "pair",
              let id = components.queryItems?.first(where: { $0.name == "id" })?.value?.lowercased(),
              id.count == 32, id.allSatisfy({ $0.isHexDigit })
        else { return nil }
        return id
    }
}

extension Data {
    var hex: String { map { String(format: "%02x", $0) }.joined() }
}
