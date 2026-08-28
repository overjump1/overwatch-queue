import Foundation

/// Which PC this device is paired with, and the secret that proves it.
///
/// The PC generates a token once and shows it as a QR code; scanning it is the whole of
/// setup. The token then goes out in every `hello`, and a server that doesn't recognise
/// it hangs up — so a flatmate on the same Wi-Fi can't drive your screen, and unpairing
/// is a button on the PC rather than a reinstall here.
public struct Pairing: Codable, Hashable, Sendable {
    /// The custom scheme in the QR code, and the app's registered URL type — so a code
    /// scanned in the system camera offers to open the app, and the Live Activity has
    /// something to point its tap at. See `DeepLink` for the routes it carries.
    public static let scheme = "owq"
    public static let defaultPort = 8787

    public var host: String
    public var port: Int
    public var token: String

    public init(host: String, port: Int = Pairing.defaultPort, token: String) {
        self.host = host
        self.port = port
        self.token = token
    }

    /// Parses what the QR code carries: `owq://pair?host=192.168.1.14&port=8787&token=…`
    ///
    /// Deliberately strict. A scanned code that is nearly right is a code from
    /// somewhere else, and the failure to report is "that isn't a pairing code" rather
    /// than a connection that never succeeds for reasons no one can see.
    public init?(pairingCode: String) {
        guard let components = URLComponents(string: pairingCode.trimmingCharacters(in: .whitespacesAndNewlines)),
              components.scheme?.lowercased() == Pairing.scheme,
              components.host?.lowercased() == "pair" || components.path == "pair" else { return nil }

        let fields = Dictionary(uniqueKeysWithValues: (components.queryItems ?? [])
            .compactMap { item in item.value.map { (item.name, $0) } })

        guard let host = fields["host"], !host.isEmpty,
              let token = fields["token"], !token.isEmpty else { return nil }

        self.host = host
        self.port = fields["port"].flatMap(Int.init) ?? Pairing.defaultPort
        self.token = token
    }

    public var socketURL: URL? {
        var components = URLComponents()
        components.scheme = "ws"
        components.host = host
        components.port = port
        components.path = "/queue"
        return components.url
    }

    public var displayText: String { "\(host):\(port)" }

    /// Enough of the token to tell two PCs apart, without putting the secret on screen.
    public var shortToken: String {
        token.count > 12 ? "\(token.prefix(8))…\(token.suffix(4))" : token
    }
}

// MARK: - Storage

public extension Pairing {
    /// Stored in the app group so the watch and the widgets can see it too.
    private static var defaults: UserDefaults {
        UserDefaults(suiteName: SharedState.appGroup) ?? .standard
    }

    private static let key = "pairing"

    static func load() -> Pairing? {
        guard let data = defaults.data(forKey: key) else { return nil }
        return try? Wire.decoder.decode(Pairing.self, from: data)
    }

    func save() {
        guard let data = try? Wire.encoder.encode(self) else { return }
        Pairing.defaults.set(data, forKey: Pairing.key)
    }

    static func forget() {
        defaults.removeObject(forKey: key)
    }
}
