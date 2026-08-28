import XCTest
@testable import OverwatchQueue

/// The pairing code is the one thing a user hands the app, and it arrives through a
/// camera — so a nearly-right code has to be refused clearly rather than turning into a
/// connection that silently never succeeds.
final class PairingTests: XCTestCase {
    private let token = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"

    private func code(host: String = "192.168.1.14", port: String? = "8787",
                      token: String? = "3f2504e0-4f89-41d3-9a0c-0305e82c3301") -> String {
        var text = "owq://pair?host=\(host)"
        if let port { text += "&port=\(port)" }
        if let token { text += "&token=\(token)" }
        return text
    }

    func testParsesWhatTheServerPrints() throws {
        let pairing = try XCTUnwrap(Pairing(pairingCode: code()))
        XCTAssertEqual(pairing.host, "192.168.1.14")
        XCTAssertEqual(pairing.port, 8787)
        XCTAssertEqual(pairing.token, token)
    }

    func testBuildsTheSocketURLTheServerListensOn() throws {
        let pairing = try XCTUnwrap(Pairing(pairingCode: code()))
        XCTAssertEqual(pairing.socketURL?.absoluteString, "ws://192.168.1.14:8787/queue")
    }

    func testMissingPortFallsBackToTheDefault() throws {
        let pairing = try XCTUnwrap(Pairing(pairingCode: code(port: nil)))
        XCTAssertEqual(pairing.port, Pairing.defaultPort)
    }

    func testSurroundingWhitespaceIsForgiven() throws {
        // Pasted codes pick up a trailing newline more often than not.
        XCTAssertNotNil(Pairing(pairingCode: "  \(code())\n"))
    }

    func testRejectsCodesThatArentOurs() {
        for text in ["https://example.com/pair?host=1.2.3.4&token=abc",   // another scheme
                     "owq://connect?host=1.2.3.4&token=abc",              // another action
                     code(token: nil),                                    // no token
                     code(host: ""),                                      // no host
                     "owq://pair",                                        // nothing at all
                     "just some text",
                     ""] {
            XCTAssertNil(Pairing(pairingCode: text), "should have refused: \(text)")
        }
    }

    func testTheTokenIsShortenedRatherThanShownInFull() throws {
        let pairing = try XCTUnwrap(Pairing(pairingCode: code()))
        XCTAssertEqual(pairing.shortToken, "3f2504e0…3301")
        XCTAssertFalse(pairing.shortToken.contains(pairing.token))
    }

    func testShortTokenLeavesAnAlreadyShortOneAlone() {
        XCTAssertEqual(Pairing(host: "h", token: "abc").shortToken, "abc")
    }

    func testHelloCarriesTheToken() throws {
        let identity = ClientIdentity(kind: .phone, name: "iPhone", appVersion: "1.0")
        let text = String(decoding: try Wire.encode(ClientCommand.hello(client: identity,
                                                                       token: token)),
                          as: UTF8.self)
        XCTAssertTrue(text.contains("\"token\":\"\(token)\""), text)

        guard case .hello(_, let decoded) = try Wire.decode(
            ClientCommand.self, from: try Wire.encode(ClientCommand.hello(client: identity,
                                                                         token: token)))
        else { return XCTFail("not a hello") }
        XCTAssertEqual(decoded, token)
    }

    func testAHelloWithoutATokenStillDecodes() throws {
        // Older clients, and anyone testing the server by hand with a raw frame.
        let raw = Data("""
        {"v":1,"body":{"type":"hello","data":{"client":\
        {"kind":"watch","name":"Watch","appVersion":"1.0"}}}}
        """.utf8)
        guard case .hello(let client, let token) = try Wire.decode(ClientCommand.self, from: raw)
        else { return XCTFail("not a hello") }
        XCTAssertEqual(client.kind, .watch)
        XCTAssertNil(token)
    }
}
