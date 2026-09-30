import Foundation

/// The Cloudflare worker: the only thing the apps talk to.
enum Worker {
    static var base: URL? {
        #if DEBUG
        if let override = UserDefaults.standard.string(forKey: "workerURL") { return URL(string: override) }
        #endif
        // QueueFox Dev carries the dev worker's address, the real app the real one (project.yml).
        if let stamped = Bundle.main.object(forInfoDictionaryKey: "QFWorkerURL") as? String {
            return URL(string: stamped)
        }
        return URL(string: "https://queuefox-push-relay.tomerady.workers.dev")
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

    /// The reply carries the current state as well, so registering doubles as a refresh.
    static func register(pairID: String, body: [String: Any]) async -> Result {
        await send("POST", path: "/v1/pair/\(pairID)/device", body: body) { data in
            try? JSONDecoder().decode(StateResponse.self, from: data).status
        }
    }

    /// Unpairing: the worker drops this phone's tokens, and the Watch's with them.
    static func forget(pairID: String) async {
        _ = await send("DELETE", path: "/v1/pair/\(pairID)/device", query: "kind=phone", body: nil) { _ in nil }
    }

    private static func send(_ method: String, path: String, query: String? = nil, body: [String: Any]?,
                             decode: (Data) -> QueueStatus?) async -> Result {
        guard let base, var components = URLComponents(url: base.appendingPathComponent(path),
                                                       resolvingAgainstBaseURL: false)
        else { return .failed }
        components.query = query
        guard let url = components.url else { return .failed }
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

    /// The links this app answers to, straight from its Info.plist. The real app takes
    /// `queuefox://` and QueueFox Dev takes `queuefox-dev://` (project.yml), so each PC app's code
    /// pairs only the matching phone app.
    ///
    /// Should that ever come back empty, the app would ignore every code it scanned, so it falls
    /// back to the links this app is known by instead, told apart by the dev app's bundle ID.
    static let schemes: [String] = {
        let types = Bundle.main.object(forInfoDictionaryKey: "CFBundleURLTypes") as? [[String: Any]] ?? []
        var declared: [String] = []
        for scheme in types.flatMap({ $0["CFBundleURLSchemes"] as? [String] ?? [] })
        where !scheme.isEmpty && !declared.contains(scheme.lowercased()) {
            declared.append(scheme.lowercased())
        }
        if !declared.isEmpty { return declared }
        return Bundle.main.bundleIdentifier?.hasSuffix(".dev") == true ? Pairing.devSchemes : Pairing.realSchemes
    }()

    private static let realSchemes = ["queuefox"]
    private static let devSchemes = ["queuefox-dev"]

    /// Accepts `queuefox://pair?id=<32 hex>` (from the QR code) and returns the pairing id.
    static func parse(_ text: String) -> String? {
        guard let components = URLComponents(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
              let scheme = components.scheme?.lowercased(), schemes.contains(scheme), components.host == "pair",
              let id = components.queryItems?.first(where: { $0.name == "id" })?.value
        else { return nil }
        return validID(id)
    }

    /// Why a scanned code won't pair, to say so on screen rather than ignore it. nil for one that
    /// does. Names the link it saw when it's some other app's, so a screenshot shows the mismatch.
    static func rejection(_ text: String) -> String? {
        guard parse(text) == nil else { return nil }
        guard let components = URLComponents(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
              let scheme = components.scheme?.lowercased(), components.host == "pair"
        else { return "That isn't a pairing code. Scan the QR code in \(appName) on your PC." }
        if schemes.contains(scheme) {
            return "That pairing code is damaged. Press Reset QR code on your PC and scan the new one."
        }
        // The other app's code. Unless this app goes by that name itself, which only a build with
        // the wrong links would, and is exactly what the last message is there to show.
        if devSchemes.contains(scheme), appName != "QueueFox Dev" {
            return "That code is from QueueFox Dev. Scan the one in \(appName) on your PC instead."
        }
        if realSchemes.contains(scheme), appName != "QueueFox" {
            return "That code is from QueueFox. Scan the one in \(appName) on your PC instead."
        }
        return "That code (\(scheme)://) isn't one \(appName) can pair with. "
            + "It takes \(schemes.map { "\($0)://" }.joined(separator: " or "))."
    }

    /// The id itself, lowercased, if it is one.
    static func validID(_ id: String) -> String? {
        let id = id.lowercased()
        return id.count == 32 && id.allSatisfy({ $0.isHexDigit }) ? id : nil
    }
}

/// What this app is called: "QueueFox", or "QueueFox Dev" for the dev app, whose PC app is the
/// one to open to pair it.
let appName = Bundle.main.object(forInfoDictionaryKey: "CFBundleDisplayName") as? String ?? "QueueFox"

extension Data {
    var hex: String { map { String(format: "%02x", $0) }.joined() }
}
