package com.tomerady.overwatchqueue.shared

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
