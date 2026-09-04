import Foundation

/// The versioned envelope every message travels in, in both directions.
/// `docs/PROTOCOL.md` is the normative description of this format for the PC server.
public struct WireEnvelope<Body: Codable & Sendable>: Codable, Sendable {
    /// Bumped only on breaking changes; a client seeing an unknown version says so
    /// rather than misreading the payload.
    public static var currentVersion: Int { 1 }

    public var v: Int
    public var body: Body

    public init(_ body: Body, v: Int = WireEnvelope<Body>.currentVersion) {
        self.v = v
        self.body = body
    }
}

/// Server → client.
public enum QueueEvent: Codable, Sendable {
    /// Full state. The server may send this at any time; it is always safe to apply.
    case snapshot(QueueSnapshot)
    /// Liveness only — carries the server clock so `ClockSync` keeps tracking drift
    /// during a long quiet queue.
    case heartbeat(serverTime: Date)
    /// The reply to `ping`, and the only message a clock offset is ever measured from.
    ///
    /// Echoes the client's own send time back untouched so the client can work out how
    /// long the round trip took, and states the server's clock at the moment of replying.
    /// Both are epoch seconds as a plain number rather than the ISO-8601 strings used
    /// everywhere else on this wire: those are written to whole seconds, and a
    /// half-second of rounding is a large error in the one message whose entire job is
    /// measuring time.
    case pong(clientTime: TimeInterval, serverTime: TimeInterval)
    /// The server rejected or couldn't fulfil a command.
    case error(code: String, message: String)
}

/// Client → server. Sent when the player acts on phone or watch; the PC side decides
/// whether to act on them (e.g. drive the in-game selection) or merely record them.
public enum ClientCommand: Codable, Sendable {
    /// First message on a new connection. Carries the pairing token the PC handed out
    /// as a QR code; a server that doesn't recognise it hangs up rather than streaming
    /// someone else's queue onto this screen.
    case hello(client: ClientIdentity, token: String?)
    case voteMap(mapKey: String)
    case selectHero(heroKey: String)
    /// Best-effort. Overwatch 2 has no accept prompt — once a match is found you are
    /// pulled in. There is a brief, unreliable window in which cancelling the queue
    /// still works, and this is a request to try it; the server may not be able to.
    case cancelQueue
    /// Ask for a fresh snapshot, e.g. after the app returns to the foreground.
    case requestSnapshot
    /// Hands over this device's APNs token, so the PC can wake it with a push once it's
    /// no longer holding this socket open. Sent once per launch, right after `hello`.
    /// `kind` says which device this token belongs to, and is not redundant with the
    /// `hello` on this socket: a watch with no socket of its own sends through the paired
    /// iPhone, and without this the server can only read the identity of whoever relayed
    /// it — filing the watch's token under `phone`, on top of the phone's own.
    case registerPushToken(token: String, environment: PushEnvironment, kind: ClientIdentity.Kind)
    /// The push token for one running Live Activity — lets the PC update or end that
    /// specific activity directly, without the phone process needing to be alive. Sent
    /// once per activity, the moment `Activity.pushTokenUpdates` first yields one.
    case registerActivityPushToken(sessionID: UUID, token: String, environment: PushEnvironment)
    /// The app-level push-to-start token — lets the PC create a Live Activity from
    /// nothing via push, even on a launch that's never opened this queue's activity
    /// itself. Independent of any one session; sent once it's available and again
    /// whenever the system hands over a new one.
    case registerActivityStartToken(token: String, environment: PushEnvironment)
    /// Asks the server to say what time it is, carrying this device's own clock so the
    /// reply can be timed. See `pong`, and `ClockSync.observe(offset:roundTrip:)` for why
    /// a measured round trip is the only honest way to do this.
    case ping(clientTime: TimeInterval)
    /// Something the device wants written into the server's log.
    ///
    /// Almost everything interesting about a Live Activity happens where nobody can see
    /// it: the card is created by the system, updated by push, and drawn by an extension
    /// in another process, so when one fails to appear there is nothing to read. Rather
    /// than reaching for the device each time, the device says so out loud — and the
    /// server window is somewhere both a developer and a player can actually look.
    ///
    /// Debug builds only, and never anything but text the app composed itself.
    case diagnostic(String)
}

/// Which of Apple's two push environments a device token is valid against — a debug
/// build's token only works against the sandbox host, a release build's only against
/// production.
public enum PushEnvironment: String, Codable, Sendable {
    case sandbox, production

    /// The build this binary was compiled as. `aps-environment` in the entitlements
    /// determines which host a token is actually issued for, and that always matches
    /// the build configuration.
    public static var current: PushEnvironment {
        #if DEBUG
        return .sandbox
        #else
        return .production
        #endif
    }
}

public struct ClientIdentity: Codable, Hashable, Sendable {
    public enum Kind: String, Codable, Sendable {
        case phone, watch
    }

    public var kind: Kind
    public var name: String
    public var appVersion: String

    public init(kind: Kind, name: String, appVersion: String) {
        self.kind = kind
        self.name = name
        self.appVersion = appVersion
    }
}

public extension Bundle {
    var appVersion: String {
        (infoDictionary?["CFBundleShortVersionString"] as? String) ?? "1.0"
    }
}

public extension Data {
    /// The lowercase hex string APNs device tokens are conventionally written as —
    /// `didRegisterForRemoteNotificationsWithDeviceToken` hands over raw bytes, and this
    /// is what actually goes in `registerPushToken`.
    var hexEncoded: String {
        map { String(format: "%02x", $0) }.joined()
    }
}

/// One coder pair used everywhere, so the phone, the watch and the PC agree on date
/// format without each surface inventing its own.
public enum Wire {
    public static let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.dateEncodingStrategy = .iso8601
        e.outputFormatting = [.sortedKeys]
        return e
    }()

    public static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .iso8601
        return d
    }()

    public static func encode<T: Codable & Sendable>(_ value: T) throws -> Data {
        try encoder.encode(WireEnvelope(value))
    }

    public static func decode<T: Codable & Sendable>(_ type: T.Type, from data: Data) throws -> T {
        let envelope = try decoder.decode(WireEnvelope<T>.self, from: data)
        guard envelope.v == WireEnvelope<T>.currentVersion else {
            throw WireError.unsupportedVersion(envelope.v)
        }
        return envelope.body
    }
}

public enum WireError: LocalizedError {
    case unsupportedVersion(Int)
    case notConnected

    public var errorDescription: String? {
        switch self {
        case .unsupportedVersion(let v):
            return "Server speaks protocol v\(v); this app speaks v\(WireEnvelope<QueueSnapshot>.currentVersion). Update one of them."
        case .notConnected:
            return "Not connected to the queue server."
        }
    }
}
