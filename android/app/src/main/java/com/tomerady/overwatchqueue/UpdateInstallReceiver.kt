package com.tomerady.overwatchqueue

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller

/**
 * How the system installer's session went. It hands back a confirmation screen to show the user
 * before anything is installed, then either finishes quietly or says what went wrong.
 */
class UpdateInstallReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, Int.MIN_VALUE)
        val message = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)
        when (status) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION")
                val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
                if (confirm == null) {
                    Updater.get(context).installFailed("The installer didn't answer")
                    return
                }
                // Started from the app's own screen, so the system lets this through.
                confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                runCatching { context.startActivity(confirm) }
                    .onFailure { Updater.get(context).installFailed(it.message ?: "Install blocked") }
            }
            // The app is about to be replaced and restarted, so there's nothing left to report.
            PackageInstaller.STATUS_SUCCESS -> Unit
            // The user said no on the confirmation screen; leave the update on offer.
            PackageInstaller.STATUS_FAILURE_ABORTED -> Updater.get(context).installCancelled()
            else -> Updater.get(context).installFailed(message ?: "Install failed")
        }
    }
}
