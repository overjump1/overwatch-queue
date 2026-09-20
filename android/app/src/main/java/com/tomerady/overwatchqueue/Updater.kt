package com.tomerady.overwatchqueue

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import android.util.Log
import com.tomerady.overwatchqueue.shared.Updates
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import java.io.File

/**
 * The app's own updates: a check against the latest GitHub release, then one tap to download the
 * APK and hand it to the system installer. Kept apart from QueueRepository, which is the queue.
 */
class Updater private constructor(context: Context) {
    /**
     * `announce` marks what the user asked for. A check they started says so and says how it went;
     * the six-hourly one in the background stays quiet unless it finds something.
     */
    sealed interface State {
        data object Idle : State

        data class Checking(val announce: Boolean) : State

        data class UpToDate(val announce: Boolean) : State

        data class Available(val version: String) : State

        data class Downloading(val percent: Int) : State

        data object Installing : State

        data class Failed(val reason: String, val announce: Boolean) : State
    }

    private val context = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val _state = MutableStateFlow<State>(State.Idle)
    val state: StateFlow<State> = _state.asStateFlow()

    /** The release on offer, kept across a failure so tapping again can pick up where it left off. */
    private var release: Updates.Release? = null
    private var checkedAt = 0L
    private var job: Job? = null

    /** True for a build that can compare itself against a release (see [Updates.checksForUpdates]). */
    val enabled: Boolean = Updates.checksForUpdates(BuildConfig.VERSION_NAME)

    /** Checks at most every six hours unless [force]d, which is the menu item. */
    fun check(force: Boolean) {
        if (!enabled || job?.isActive == true) return
        if (!force && checkedAt != 0L && System.currentTimeMillis() - checkedAt < CHECK_INTERVAL_MS) return
        // A downloaded update stays on offer; checking again would only find the same release.
        if (downloadedApk() != null) return
        job = scope.launch {
            _state.value = State.Checking(force)
            val result = Updates.latest(BuildConfig.VERSION_NAME)
            if (result !is Updates.Result.Ok) {
                // Not counted as a check, so the next one isn't six hours away.
                _state.value = State.Failed("Couldn't check for updates", announce = force)
                return@launch
            }
            checkedAt = System.currentTimeMillis()
            release = result.release
            val found = result.release
            if (found != null) {
                Log.i(TAG, "Update available: ${found.version}")
                _state.value = State.Available(found.version)
                return@launch
            }
            _state.value = State.UpToDate(force)
            // "You're on the latest version" has been read by now; don't leave it sitting there.
            if (force) {
                delay(ANNOUNCE_MS)
                if (_state.value == State.UpToDate(true)) _state.value = State.Idle
            }
        }
    }

    /** The update row was tapped: install what's on offer, or have another go at what failed. */
    fun tap() {
        if (job?.isActive == true) return
        if (release == null) check(force = true) else install()
    }

    private fun install() {
        val found = release ?: return
        // Android only lets an app install APKs once the user allows it for this app specifically.
        if (!context.packageManager.canRequestPackageInstalls()) {
            askToAllowInstalls()
            return
        }
        job = scope.launch {
            val apk = downloadedApk() ?: run {
                _state.value = State.Downloading(0)
                runCatching {
                    Updates.download(context, found) { percent ->
                        _state.value = State.Downloading(percent)
                    }
                }.getOrElse {
                    Log.w(TAG, "Downloading the update failed", it)
                    _state.value = State.Failed("Download failed", announce = true)
                    return@launch
                }
            }
            _state.value = State.Installing
            runCatching { Updates.install(context, apk, statusTarget()) }.onFailure {
                Log.w(TAG, "Handing the APK to the installer failed", it)
                _state.value = State.Failed("Couldn't start the install", announce = true)
            }
        }
    }

    /** From UpdateInstallReceiver, on its way back from the system installer. */
    fun installFailed(reason: String) {
        Log.w(TAG, "Install failed: $reason")
        _state.value = State.Failed(reason, announce = true)
    }

    /** The user declined the system installer's confirmation, so the update stays on offer. */
    fun installCancelled() {
        release?.let { _state.value = State.Available(it.version) }
    }

    private fun askToAllowInstalls() {
        val settings = Intent(
            Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
            Uri.parse("package:${context.packageName}"),
        ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        runCatching { context.startActivity(settings) }.onFailure {
            _state.value = State.Failed("Allow installing apps for OW Queue in Settings", announce = true)
        }
    }

    private fun downloadedApk(): File? = release?.let {
        File(File(context.cacheDir, "updates"), "OWQueue-${it.version}.apk").takeIf { apk -> apk.isFile }
    }

    private fun statusTarget(): PendingIntent = PendingIntent.getBroadcast(
        context,
        0,
        Intent(context, UpdateInstallReceiver::class.java).setPackage(context.packageName),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE,
    )

    companion object {
        private const val TAG = "OWQueue"
        private const val CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000L
        private const val ANNOUNCE_MS = 4_000L

        @Volatile private var instance: Updater? = null

        fun get(context: Context): Updater =
            instance ?: synchronized(this) { instance ?: Updater(context).also { instance = it } }
    }
}
