import AVFoundation

/// Plays the match-found cue. Uses `match_found.caf` from the bundle — replace that file
/// to change the sound; nothing else needs to know.
@MainActor
public final class SoundPlayer {
    public static let shared = SoundPlayer()

    private var player: AVAudioPlayer?

    private init() {}

    public func playMatchFound() {
        guard let url = Bundle.main.url(forResource: "match_found", withExtension: "caf") else { return }
        do {
            // `.ambient` with `.mixWithOthers` so the cue never pauses the user's music —
            // people queue with something playing, and interrupting it would be worse
            // than missing the sound.
            try AVAudioSession.sharedInstance().setCategory(.ambient, options: [.mixWithOthers])
            try AVAudioSession.sharedInstance().setActive(true)

            let player = try AVAudioPlayer(contentsOf: url)
            player.volume = 0.9
            player.prepareToPlay()
            player.play()
            self.player = player
        } catch {
            return
        }
    }
}
