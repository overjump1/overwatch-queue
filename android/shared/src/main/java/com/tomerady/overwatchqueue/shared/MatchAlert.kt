package com.tomerady.overwatchqueue.shared

import android.content.Context
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager

/** Vibration plus sound for a match found while the app is open. */
class MatchAlert(context: Context) {
    private val context = context.applicationContext
    private var player: MediaPlayer? = null

    fun play() {
        vibrate()
        player?.release()
        player = MediaPlayer.create(
            context,
            R.raw.match_found,
            AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_NOTIFICATION_EVENT)
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                .build(),
            0,
        )?.apply {
            setOnCompletionListener {
                it.release()
                if (player === it) player = null
            }
            start()
        }
    }

    private fun vibrate() {
        val vibrator = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            context.getSystemService(VibratorManager::class.java)?.defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            context.getSystemService(Vibrator::class.java)
        }
        vibrator?.vibrate(VibrationEffect.createWaveform(QueueNotifier.VIBRATION, -1))
    }
}
