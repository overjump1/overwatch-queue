package com.tomerady.overwatchqueue.shared

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.ContentResolver
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

/**
 * The ongoing queue notification: Android's version of the iPhone's Live Activity.
 *
 * It's one notification whose channel decides whether a post makes a sound: quiet updates go to
 * [STATUS_CHANNEL], a queue starting to [QUEUE_CHANNEL] and a match being found to [MATCH_CHANNEL].
 * Users can tune each one in the system settings.
 */
object QueueNotifier {
    const val STATUS_CHANNEL = "status"
    const val QUEUE_CHANNEL = "queue"
    const val MATCH_CHANNEL = "match"

    /** Three pulses, like the iPhone's match haptics. */
    val VIBRATION = longArrayOf(0, 100, 250, 100, 250, 100)

    private const val ID = 1
    private const val STATE_EXTRA = "owq.state"
    /** `Notification.EXTRA_REQUEST_PROMOTED_ONGOING` (API 36): shows it as a Live Update on Android 16. */
    private const val PROMOTED_EXTRA = "android.requestPromotedOngoing"

    private var alertedFoundAt: Double? = null

    /**
     * Claims the one alert a match gets, whichever of the push and the open app sees it first.
     * Returns false if this match already alerted.
     */
    @Synchronized
    fun claimMatchAlert(foundAt: Double?): Boolean {
        if (foundAt == null || foundAt == alertedFoundAt) return false
        alertedFoundAt = foundAt
        return true
    }

    fun createChannels(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java)
        val status = NotificationChannel(STATUS_CHANNEL, "Queue status", NotificationManager.IMPORTANCE_LOW).apply {
            description = "The timer while you're in a queue or a match"
            setShowBadge(false)
        }
        val queue = NotificationChannel(QUEUE_CHANNEL, "Queue started", NotificationManager.IMPORTANCE_DEFAULT).apply {
            description = "When your PC starts a queue"
            setShowBadge(false)
        }
        val match = NotificationChannel(MATCH_CHANNEL, "Match found", NotificationManager.IMPORTANCE_HIGH).apply {
            description = "When a match is found"
            setSound(
                soundUri(context),
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_NOTIFICATION_EVENT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build(),
            )
            enableVibration(true)
            vibrationPattern = VIBRATION
            setShowBadge(false)
        }
        manager.createNotificationChannels(listOf(status, queue, match))
    }

    /** Shows or updates the notification for a queue or match. [alert] makes it sound. */
    fun show(context: Context, status: QueueStatus, alert: QueuePush.Alert? = null) {
        if (status.state == QueueState.IDLE) {
            end(context, 0)
            return
        }
        val channel = when {
            alert == QueuePush.Alert.FOUND && claimMatchAlert(status.foundAt) -> MATCH_CHANNEL
            alert == QueuePush.Alert.QUEUE -> QUEUE_CHANNEL
            else -> STATUS_CHANNEL
        }
        val builder = base(context, channel, status)
            .setOngoing(true)
            .setCategory(if (status.state == QueueState.FOUND) NotificationCompat.CATEGORY_EVENT else NotificationCompat.CATEGORY_PROGRESS)
            .setPriority(if (channel == MATCH_CHANNEL) NotificationCompat.PRIORITY_HIGH else NotificationCompat.PRIORITY_LOW)
            .setSilent(channel == STATUS_CHANNEL)
            .addExtras(Bundle().apply { putBoolean(PROMOTED_EXTRA, true) })

        when (status.state) {
            QueueState.QUEUEING -> builder
                .setContentText(status.modeName)
                .timer(status.startedAt)
            QueueState.FOUND -> builder
                .setContentText(status.waited?.let { "${status.modeName} · waited ${clockString(it)}" } ?: status.modeName)
                .setSubText("Head back to your PC")
                .setShowWhen(false)
            QueueState.PLAYING -> builder
                .setContentText("Good luck, have fun!")
                .setSubText("${status.modeName} · in match")
                .timer(status.foundAt)
            QueueState.IDLE -> Unit
        }
        post(context, builder)
    }

    /**
     * Ends the queue notification, leaving "Not in queue" up for [lingerSeconds].
     * Does nothing if no queue or match is showing.
     */
    fun end(context: Context, lingerSeconds: Long) {
        if (!isShowing(context)) return
        if (lingerSeconds <= 0) {
            cancel(context)
            return
        }
        val builder = base(context, STATUS_CHANNEL, QueueStatus.Idle)
            .setContentText(QueueStatus.Idle.subtitle)
            .setSilent(true)
            .setAutoCancel(true)
            .setShowWhen(false)
            .setTimeoutAfter(lingerSeconds * 1000)
        post(context, builder)
    }

    fun cancel(context: Context) {
        NotificationManagerCompat.from(context).cancel(ID)
    }

    /** True while a queue or match notification is up (not a lingering "Not in queue"). */
    fun isShowing(context: Context): Boolean =
        context.getSystemService(NotificationManager::class.java).activeNotifications.any {
            it.id == ID && it.notification.extras.getString(STATE_EXTRA) != QueueState.IDLE.name
        }

    fun soundUri(context: Context): Uri =
        Uri.parse("${ContentResolver.SCHEME_ANDROID_RESOURCE}://${context.packageName}/${R.raw.match_found}")

    private fun base(context: Context, channel: String, status: QueueStatus): NotificationCompat.Builder =
        NotificationCompat.Builder(context, channel)
            .setSmallIcon(R.drawable.ic_stat_queue)
            .setContentTitle(status.title)
            .setColor(status.accent)
            .setOnlyAlertOnce(channel == STATUS_CHANNEL)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setContentIntent(openApp(context))
            .addExtras(Bundle().apply { putString(STATE_EXTRA, status.state.name) })

    private fun NotificationCompat.Builder.timer(since: Double?): NotificationCompat.Builder =
        if (since == null) setShowWhen(false)
        else setShowWhen(true).setWhen((since * 1000).toLong()).setUsesChronometer(true)

    private fun openApp(context: Context): PendingIntent? {
        val intent = context.packageManager.getLaunchIntentForPackage(context.packageName) ?: return null
        return PendingIntent.getActivity(
            context, 0, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
    }

    private fun post(context: Context, builder: NotificationCompat.Builder) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return
        try {
            NotificationManagerCompat.from(context).notify(ID, builder.build())
        } catch (e: SecurityException) {
            // Notifications were turned off between the check and the post.
        }
    }
}
