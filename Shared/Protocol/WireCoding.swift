import Foundation

/// Explicit JSON shapes for the three enums that cross the wire.
///
/// Swift's synthesized `Codable` for an enum with associated values produces
/// `{"searching":{"_0":{…}}}` — the `_0` is a compiler detail, and a Windows server
/// written by hand shouldn't have to reproduce it (or break when a refactor changes it).
///
/// Everything therefore encodes as a tagged object:
///
///     {"type": "searching", "data": { … }}
///     {"type": "idle"}
///
/// `docs/PROTOCOL.md` is the normative reference, and `WireFormatTests` pins these
/// shapes so a Swift-side change can't silently break the server.
private enum TaggedKeys: String, CodingKey {
    case type, data
}

/// Shared helpers so all three enums tag themselves identically.
private extension KeyedEncodingContainer where K == TaggedKeys {
    mutating func tag(_ type: String) throws {
        try encode(type, forKey: .type)
    }
}

// MARK: - QueuePhase

extension QueuePhase {
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: TaggedKeys.self)
        try c.tag(kind.rawValue)
        switch self {
        case .idle:                     break
        case .searching(let i):         try c.encode(i, forKey: .data)
        case .matchFound(let i):        try c.encode(i, forKey: .data)
        case .mapVote(let i):           try c.encode(i, forKey: .data)
        case .heroSelect(let i):        try c.encode(i, forKey: .data)
        case .inGame(let i):            try c.encode(i, forKey: .data)
        case .cancelled(let i):         try c.encode(i, forKey: .data)
        }
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: TaggedKeys.self)
        let raw = try c.decode(String.self, forKey: .type)
        guard let kind = Kind(rawValue: raw) else {
            throw DecodingError.dataCorruptedError(
                forKey: .type, in: c,
                debugDescription: "Unknown phase \"\(raw)\". Known: \(Kind.allCases.map(\.rawValue).joined(separator: ", "))")
        }
        switch kind {
        case .idle:        self = .idle
        case .searching:   self = .searching(try c.decode(SearchInfo.self, forKey: .data))
        case .matchFound:  self = .matchFound(try c.decode(MatchFoundInfo.self, forKey: .data))
        case .mapVote:     self = .mapVote(try c.decode(MapVoteInfo.self, forKey: .data))
        case .heroSelect:  self = .heroSelect(try c.decode(HeroSelectInfo.self, forKey: .data))
        case .inGame:      self = .inGame(try c.decode(InGameInfo.self, forKey: .data))
        case .cancelled:   self = .cancelled(try c.decode(CancelInfo.self, forKey: .data))
        }
    }
}

// MARK: - QueueEvent

extension QueueEvent {
    private struct Heartbeat: Codable { var serverTime: Date }
    private struct ErrorBody: Codable { var code: String; var message: String }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: TaggedKeys.self)
        switch self {
        case .snapshot(let s):
            try c.tag("snapshot")
            try c.encode(s, forKey: .data)
        case .heartbeat(let time):
            try c.tag("heartbeat")
            try c.encode(Heartbeat(serverTime: time), forKey: .data)
        case .error(let code, let message):
            try c.tag("error")
            try c.encode(ErrorBody(code: code, message: message), forKey: .data)
        }
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: TaggedKeys.self)
        switch try c.decode(String.self, forKey: .type) {
        case "snapshot":
            self = .snapshot(try c.decode(QueueSnapshot.self, forKey: .data))
        case "heartbeat":
            self = .heartbeat(serverTime: try c.decode(Heartbeat.self, forKey: .data).serverTime)
        case "error":
            let body = try c.decode(ErrorBody.self, forKey: .data)
            self = .error(code: body.code, message: body.message)
        case let other:
            throw DecodingError.dataCorruptedError(
                forKey: .type, in: c,
                debugDescription: "Unknown event \"\(other)\". Known: snapshot, heartbeat, error")
        }
    }
}

// MARK: - ClientCommand

extension ClientCommand {
    private struct MapKey: Codable { var mapKey: String }
    private struct HeroKey: Codable { var heroKey: String }
    private struct Hello: Codable { var token: String?; var client: ClientIdentity }
    private struct PushToken: Codable { var token: String; var environment: PushEnvironment }
    private struct ActivityPushToken: Codable {
        var sessionID: UUID; var token: String; var environment: PushEnvironment
    }
    private struct Diagnostic: Codable { var message: String }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: TaggedKeys.self)
        switch self {
        case .hello(let identity, let token):
            try c.tag("hello")
            try c.encode(Hello(token: token, client: identity), forKey: .data)
        case .voteMap(let key):
            try c.tag("voteMap")
            try c.encode(MapKey(mapKey: key), forKey: .data)
        case .selectHero(let key):
            try c.tag("selectHero")
            try c.encode(HeroKey(heroKey: key), forKey: .data)
        case .cancelQueue:
            try c.tag("cancelQueue")
        case .requestSnapshot:
            try c.tag("requestSnapshot")
        case .registerPushToken(let token, let environment):
            try c.tag("registerPushToken")
            try c.encode(PushToken(token: token, environment: environment), forKey: .data)
        case .registerActivityPushToken(let sessionID, let token, let environment):
            try c.tag("registerActivityPushToken")
            try c.encode(ActivityPushToken(sessionID: sessionID, token: token, environment: environment),
                        forKey: .data)
        case .registerActivityStartToken(let token, let environment):
            try c.tag("registerActivityStartToken")
            try c.encode(PushToken(token: token, environment: environment), forKey: .data)
        case .diagnostic(let message):
            try c.tag("diagnostic")
            try c.encode(Diagnostic(message: message), forKey: .data)
        }
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: TaggedKeys.self)
        switch try c.decode(String.self, forKey: .type) {
        case "hello":
            let hello = try c.decode(Hello.self, forKey: .data)
            self = .hello(client: hello.client, token: hello.token)
        case "voteMap":
            self = .voteMap(mapKey: try c.decode(MapKey.self, forKey: .data).mapKey)
        case "selectHero":
            self = .selectHero(heroKey: try c.decode(HeroKey.self, forKey: .data).heroKey)
        case "cancelQueue":
            self = .cancelQueue
        case "requestSnapshot":
            self = .requestSnapshot
        case "registerPushToken":
            let push = try c.decode(PushToken.self, forKey: .data)
            self = .registerPushToken(token: push.token, environment: push.environment)
        case "registerActivityPushToken":
            let push = try c.decode(ActivityPushToken.self, forKey: .data)
            self = .registerActivityPushToken(sessionID: push.sessionID, token: push.token,
                                              environment: push.environment)
        case "registerActivityStartToken":
            let push = try c.decode(PushToken.self, forKey: .data)
            self = .registerActivityStartToken(token: push.token, environment: push.environment)
        case "diagnostic":
            self = .diagnostic(try c.decode(Diagnostic.self, forKey: .data).message)
        case let other:
            throw DecodingError.dataCorruptedError(
                forKey: .type, in: c,
                debugDescription: "Unknown command \"\(other)\"")
        }
    }
}
