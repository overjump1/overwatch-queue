package com.tomerady.overqueue.shared

/** An FCM data message from the worker (`androidPayload` in worker/src/logic.ts). */
data class QueuePush(
    val event: Event,
    val status: QueueStatus,
    val alert: Alert?,
    /** For `END`: seconds "Not in queue" stays up. */
    val linger: Long,
    /** Unix milliseconds, to drop pushes that arrive out of order. */
    val sentAt: Long,
) {
    enum class Event { START, UPDATE, END }

    enum class Alert { QUEUE, FOUND }

    companion object {
        fun parse(data: Map<String, String>): QueuePush? {
            val event = when (data["event"]) {
                "start" -> Event.START
                "update" -> Event.UPDATE
                "end" -> Event.END
                else -> return null
            }
            val status = data["status"]?.let(QueueStatus::decode) ?: return null
            val alert = when (data["alert"]) {
                "queue" -> Alert.QUEUE
                "found" -> Alert.FOUND
                else -> null
            }
            return QueuePush(
                event = event,
                status = status,
                alert = alert,
                linger = data["linger"]?.toLongOrNull() ?: 0,
                sentAt = data["sentAt"]?.toLongOrNull() ?: 0,
            )
        }
    }
}

/**
 * The PC's notification speed test (`startTest` in worker/src/index.ts): shown like a match alert,
 * and answered straight away so the PC can time how long it took to get here.
 */
data class TestPush(val testId: String, val pairId: String) {
    companion object {
        private val ID = Regex("^[0-9a-f]{32}$")

        fun parse(data: Map<String, String>): TestPush? {
            if (data["event"] != "test") return null
            val testId = data["test"]?.takeIf(ID::matches) ?: return null
            val pairId = data["pair"]?.takeIf(ID::matches) ?: return null
            return TestPush(testId, pairId)
        }
    }
}
