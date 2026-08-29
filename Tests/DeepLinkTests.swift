import XCTest
@testable import OverwatchQueue

/// Two kinds of URL arrive at the app and mean opposite things — a pairing code to be
/// parsed, and the Live Activity asking to be opened. Telling them apart is what stops the
/// second one being fed to the pairing parser and reported as a bad code.
final class DeepLinkTests: XCTestCase {
    private let code = "owq://pair?host=192.168.1.14&port=8787&token=3f2504e0-4f89-41d3-9a0c-0305e82c3301"

    func testRoutesThePairingCodeToPairing() throws {
        let url = try XCTUnwrap(URL(string: code))
        guard case .pair(let carried) = DeepLink.parse(url) else {
            return XCTFail("a pairing code should route to .pair")
        }
        // Handed on whole, because `Pairing` is the only thing allowed to judge it.
        XCTAssertEqual(carried, code)
        XCTAssertNotNil(Pairing(pairingCode: carried))
    }

    func testRoutesTheOpenLinkToOpen() throws {
        let url = try XCTUnwrap(URL(string: "owq://open"))
        XCTAssertEqual(DeepLink.parse(url), .open)
    }

    /// The one that matters: the widget used to point at a scheme the app doesn't
    /// register, so the tap went nowhere at all. Building `openURL` from `Pairing.scheme`
    /// is what stops that recurring — this asserts the two agree.
    func testTheActivitysOwnURLRoundTrips() {
        XCTAssertEqual(DeepLink.parse(DeepLink.openURL), .open)
        XCTAssertEqual(DeepLink.openURL.scheme, Pairing.scheme)
    }

    func testRejectsTheSchemeTheWidgetUsedToUse() throws {
        let url = try XCTUnwrap(URL(string: "owqueue://open"))
        XCTAssertEqual(DeepLink.parse(url), .unrecognised)
    }

    func testRejectsAnUnrelatedURL() throws {
        let url = try XCTUnwrap(URL(string: "https://example.com/open"))
        XCTAssertEqual(DeepLink.parse(url), .unrecognised)
    }

    func testRejectsOurSchemeWithAnUnknownRoute() throws {
        let url = try XCTUnwrap(URL(string: "owq://something-else"))
        XCTAssertEqual(DeepLink.parse(url), .unrecognised)
    }

    /// `owq:pair?…` has the word in `path` rather than `host`. `Pairing` accepts both, so
    /// this has to as well or a code that scans would route nowhere.
    func testAcceptsTheSchemeWithoutSlashes() throws {
        let url = try XCTUnwrap(URL(string: "owq:pair?host=10.0.0.2&token=abc"))
        guard case .pair = DeepLink.parse(url) else {
            return XCTFail("a slash-less pairing code should still route to .pair")
        }
    }

    func testIsCaseInsensitiveAboutTheScheme() throws {
        let url = try XCTUnwrap(URL(string: "OWQ://OPEN"))
        XCTAssertEqual(DeepLink.parse(url), .open)
    }
}
