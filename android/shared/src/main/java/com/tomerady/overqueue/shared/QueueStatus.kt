package com.tomerady.overqueue.shared

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.util.Locale
import kotlin.math.max
import kotlin.math.roundToInt

@Serializable
enum class QueueState {
    @SerialName("idle") IDLE,
    @SerialName("queueing") QUEUEING,
    @SerialName("found") FOUND,

    /** What `found` turns into a minute after the match is found. */
    @SerialName("playing") PLAYING,
}

@Serializable
enum class GameMode(val displayName: String, val color: Int) {
    @SerialName("quickPlay") QUICK_PLAY("Quick Play", rgb(0.231, 0.580, 0.965)),
    @SerialName("competitive") COMPETITIVE("Competitive", rgb(0.925, 0.278, 0.494)),
    @SerialName("arcade") ARCADE("Arcade", rgb(0.302, 0.800, 0.518)),
    @SerialName("stadium") STADIUM("Stadium", rgb(1.0, 0.780, 0.298)),
    @SerialName("mysteryHeroes") MYSTERY_HEROES("Mystery Heroes", rgb(0.639, 0.463, 0.957)),
    @SerialName("custom") CUSTOM("Custom Game", rgb(0.62, 0.62, 0.62)),
}

/** The queue as the worker reports it. Times are unix seconds. */
@Serializable
data class QueueStatus(
    val state: QueueState = QueueState.IDLE,
    val mode: GameMode? = null,
    val startedAt: Double? = null,
    val foundAt: Double? = null,
) {
    val title: String
        get() = when (state) {
            QueueState.IDLE -> "Not in queue"
            QueueState.QUEUEING -> "In queue"
            QueueState.FOUND -> "Match found!"
            QueueState.PLAYING -> "In a match"
        }

    val subtitle: String
        get() = when (state) {
            QueueState.IDLE -> "Start a queue on your PC"
            QueueState.QUEUEING, QueueState.FOUND -> modeName
            QueueState.PLAYING -> "Good luck, have fun!"
        }

    val modeName: String get() = mode?.displayName ?: "Overwatch"

    /** ARGB. */
    val accent: Int
        get() = when (state) {
            QueueState.IDLE -> rgb(0.5, 0.5, 0.5)
            QueueState.QUEUEING, QueueState.PLAYING -> mode?.color ?: WHITE
            QueueState.FOUND -> GREEN
        }

    val inMatch: Boolean get() = state == QueueState.FOUND || state == QueueState.PLAYING

    /** Seconds from the queue starting to the match being found. */
    val waited: Double?
        get() = if (startedAt != null && foundAt != null) max(0.0, foundAt - startedAt) else null

    /** True for a match found moments ago, so opening the app later doesn't re-alert. */
    fun isFreshMatch(now: Double = nowSeconds()): Boolean =
        state == QueueState.FOUND && foundAt != null && now - foundAt < 60

    companion object {
        val Idle = QueueStatus()

        const val WHITE = 0xFFFFFFFF.toInt()
        /** iOS's dark-mode system green, to match the iPhone app. */
        const val GREEN = 0xFF30D158.toInt()

        internal val json = Json {
            ignoreUnknownKeys = true
            coerceInputValues = true
        }

        /** Lenient like the iPhone app: unknown states and modes read as idle and no mode. */
        fun decode(text: String): QueueStatus? =
            runCatching { json.decodeFromString(serializer(), text) }.getOrNull()
    }
}

fun nowSeconds(): Double = System.currentTimeMillis() / 1000.0

/** `m:ss`, or `h:mm:ss` from an hour on. */
fun clockString(seconds: Double): String {
    val total = max(0, seconds.toInt())
    return if (total >= 3600) {
        String.format(Locale.ROOT, "%d:%02d:%02d", total / 3600, total / 60 % 60, total % 60)
    } else {
        String.format(Locale.ROOT, "%d:%02d", total / 60, total % 60)
    }
}

private fun rgb(r: Double, g: Double, b: Double): Int =
    (0xFF shl 24) or ((r * 255).roundToInt() shl 16) or ((g * 255).roundToInt() shl 8) or (b * 255).roundToInt()
