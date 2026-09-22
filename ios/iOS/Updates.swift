import Foundation

/// What the update line at the bottom of the queue screen says. `check()` returns one of the last
/// three; `.quiet` is the resting state and `.checking` is set while a check the user asked for runs.
enum UpdateNotice: Equatable {
    case quiet
    case checking
    case available(String)
    case upToDate
    case failed
}

/// Whether a newer build is out, from the GitHub releases the Release workflow publishes.
///
/// iOS can't install an app on itself, so this only reports the version; getting the build onto the
/// phone is still AltStore or Sideloadly with the IPA from the release page.
enum Updates {
    static let releaseAPI = URL(string: "https://api.github.com/repos/overjump1/overwatch-queue/releases/latest")!
    private static let assetPrefix = "OverQueue-"
    private static let assetSuffix = ".ipa"

    private struct Release: Decodable {
        let assets: [Asset]

        struct Asset: Decodable {
            let name: String
        }
    }

    static var current: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? ""
    }

    /// `v2.0.42` becomes [2, 0, 42]. A trailing non-number ends it, so `0.0.0-dev` is [0, 0, 0].
    static func parse(_ version: String?) -> [Int]? {
        let trimmed = (version ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .drop { $0 == "v" || $0 == "V" }
        var parts: [Int] = []
        for piece in trimmed.split(whereSeparator: { $0 == "." || $0 == "-" || $0 == "+" }) {
            guard piece.allSatisfy(\.isNumber), let number = Int(piece) else { break }
            parts.append(number)
        }
        guard !parts.isEmpty else { return nil }
        return Array((parts + [0, 0, 0]).prefix(3))
    }

    static func isNewer(_ candidate: String?, than current: String?) -> Bool {
        guard let left = parse(candidate), let right = parse(current) else { return false }
        for (mine, theirs) in zip(left, right) where mine != theirs { return mine > theirs }
        return false
    }

    /// Written by .github/workflows/ios-app.yml, so only the IPA on the release page carries it.
    /// That build is the one whose updates live on GitHub; TestFlight hands out its own and the
    /// App Store hands out its own, and an App Store build that pointed anyone at a release page
    /// would be breaking App Review's rules as well as wasting their time. Opting in beats opting
    /// out here: a build nobody stamped keeps quiet instead of guessing.
    static var isGitHubReleaseBuild: Bool {
        Bundle.main.object(forInfoDictionaryKey: "OWQGitHubBuild") as? Bool ?? false
    }

    /// Worked out once, since the view body reads it and it can't change while the app runs. On
    /// for the release IPA alone, off for Debug builds, and off for the 0.0.0 a build nobody
    /// stamped keeps — below every release, so it would claim an update forever.
    static let checksForUpdates: Bool = {
        #if DEBUG
        return false
        #else
        return isGitHubReleaseBuild && (parse(current)?.first ?? 0) > 0
        #endif
    }()

    static func check() async -> UpdateNotice {
        guard checksForUpdates else { return .upToDate }
        var request = URLRequest(url: releaseAPI, timeoutInterval: 20)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        request.setValue("OverQueue/\(current)", forHTTPHeaderField: "User-Agent")
        guard let (data, response) = try? await URLSession.shared.data(for: request),
              (response as? HTTPURLResponse)?.statusCode == 200,
              let release = try? JSONDecoder().decode(Release.self, from: data)
        else { return .failed }
        guard let version = newerVersion(in: release.assets.map(\.name), than: current) else {
            return .upToDate
        }
        return .available(version)
    }

    /// The version of the newer IPA among `assetNames`, if there is one.
    ///
    /// The version comes from the asset's filename, not the release tag: release.yml only rebuilds
    /// the apps that changed and copies the rest forward, so v2.0.3 holds OverQueue-2.0.2.ipa and
    /// going by the tag would claim an update this build already is.
    static func newerVersion(in assetNames: [String], than current: String) -> String? {
        for name in assetNames where name.hasPrefix(assetPrefix) && name.hasSuffix(assetSuffix) {
            let version = String(name.dropFirst(assetPrefix.count).dropLast(assetSuffix.count))
            return isNewer(version, than: current) ? version : nil
        }
        return nil
    }
}
