package com.tomerady.overwatchqueue

import android.content.Context
import android.util.Log
import androidx.core.app.NotificationManagerCompat
import com.google.firebase.messaging.FirebaseMessaging
import com.tomerady.overwatchqueue.shared.MatchAlert
import com.tomerady.overwatchqueue.shared.Pairing
import com.tomerady.overwatchqueue.shared.QueueNotifier
import com.tomerady.overwatchqueue.shared.QueuePush
import com.tomerady.overwatchqueue.shared.QueueState
import com.tomerady.overwatchqueue.shared.QueueStatus
import com.tomerady.overwatchqueue.shared.Worker
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** The app's state: the pairing, the queue, and keeping the notification in step. Mirrors AppModel on iOS. */
class QueueRepository private constructor(context: Context) {
    data class UiState(
        val pairId: String?,
        val status: QueueStatus = QueueStatus.Idle,
        val reachable: Boolean = true,
        val pairingWasReset: Boolean = false,
        /** Goes up by one for each match found while the app is open, to flash the screen. */
        val matchAlerts: Int = 0,
        /** Why notifications won't arrive, if they won't. Shown on the queue screen. */
        val notificationProblem: NotificationProblem? = null,
    )

    enum class NotificationProblem {
        /** The user turned the app's notifications off. */
        DISABLED,

        /** The worker doesn't have this phone's push token yet, so it can't send it anything. */
        NOT_REGISTERED,
    }

    private val context = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val alerts = MatchAlert(this.context)
    private val _state = MutableStateFlow(UiState(pairId = Pairing.load(this.context)))
    val state: StateFlow<UiState> = _state.asStateFlow()

    @Volatile private var fcmToken: String? = null
    /** The pairing and token the worker last accepted; anything else still needs registering. */
    @Volatile private var registeredAs: Pair<String, String>? = null
    private var registerJob: Job? = null
    private var active = false
    private var lastPushAt = 0L
    /** The match the screen already flashed for, so the same match never flashes twice. */
    private var flashedFoundAt: Double? = null

    fun start() {
        requestFcmToken()
    }

    private fun requestFcmToken() {
        try {
            FirebaseMessaging.getInstance().token
                .addOnSuccessListener { setFcmToken(it) }
                .addOnFailureListener { Log.w(TAG, "No FCM token", it) }
        } catch (e: IllegalStateException) {
            // Firebase isn't configured in this build (no google-services.json).
            Log.w(TAG, "Firebase isn't set up", e)
        }
    }

    /** Polls the worker every 2 seconds until cancelled, while the app is on screen. */
    suspend fun poll() {
        active = true
        try {
            while (true) {
                // Retried on every tick until it sticks: a phone the worker doesn't know gets no pushes.
                if (fcmToken == null) requestFcmToken() else if (!isRegistered()) register()
                refresh()
                updateNotificationProblem()
                delay(2_000)
            }
        } finally {
            active = false
        }
    }

    fun setFcmToken(token: String) {
        if (token == fcmToken) return
        fcmToken = token
        registerSoon()
    }

    /** Returns false if [text] isn't a pairing code. */
    fun pair(text: String): Boolean {
        val id = Pairing.parse(text) ?: return false
        Pairing.save(context, id)
        _state.update { it.copy(pairId = id, pairingWasReset = false, status = QueueStatus.Idle) }
        registerSoon()
        scope.launch { refresh() }
        return true
    }

    fun unpair() {
        registerJob?.cancel()
        registeredAs = null
        Pairing.save(context, null)
        _state.update { it.copy(pairId = null, status = QueueStatus.Idle) }
        QueueNotifier.cancel(context)
    }

    /** A push from the worker. Called on FCM's thread, so the notification is posted right away. */
    fun onPush(push: QueuePush) {
        synchronized(this) {
            if (push.sentAt < lastPushAt) return
            lastPushAt = push.sentAt
        }
        if (_state.value.pairId == null) return
        if (push.event == QueuePush.Event.END) {
            QueueNotifier.end(context, push.linger)
        } else {
            QueueNotifier.show(context, push.status, push.alert)
        }
        scope.launch { apply(push.status, fromPush = true) }
    }

    // Worker

    private suspend fun refresh() {
        val id = _state.value.pairId ?: return
        when (val result = Worker.fetchStatus(id)) {
            is Worker.Result.Ok -> {
                _state.update { it.copy(reachable = true) }
                val status = result.status
                if (status != null && id == _state.value.pairId) apply(status, fromPush = false)
            }
            Worker.Result.Reset -> handleReset(id)
            is Worker.Result.Failed -> _state.update { it.copy(reachable = false) }
        }
    }

    private fun isRegistered(): Boolean {
        val id = _state.value.pairId ?: return true
        val token = fcmToken ?: return false
        return registeredAs == id to token
    }

    /**
     * Registers now, and keeps retrying with a growing wait until the worker accepts it. Also runs
     * with the app closed (a new FCM token arrives in the background), when nothing polls.
     */
    @Synchronized
    private fun registerSoon() {
        registerJob?.cancel()
        registerJob = scope.launch {
            var wait = 5_000L
            while (!register()) {
                delay(wait)
                wait = (wait * 2).coerceAtMost(5 * 60_000L)
            }
        }
    }

    /** Returns true once the worker has this phone's token, or there's nothing to register. */
    private suspend fun register(): Boolean {
        val id = _state.value.pairId ?: return true
        val token = fcmToken ?: return true
        if (registeredAs == id to token) return true
        val body = buildJsonObject {
            put("kind", "android")
            put("fcm", token)
        }
        return when (val result = Worker.register(id, body)) {
            is Worker.Result.Ok -> {
                registeredAs = id to token
                updateNotificationProblem()
                true
            }
            Worker.Result.Reset -> {
                handleReset(id)
                true
            }
            is Worker.Result.Failed -> {
                Log.w(TAG, "Registering with the worker failed: ${result.reason}")
                false
            }
        }
    }

    private fun updateNotificationProblem() {
        val problem = when {
            !NotificationManagerCompat.from(context).areNotificationsEnabled() -> NotificationProblem.DISABLED
            _state.value.pairId != null && !isRegistered() -> NotificationProblem.NOT_REGISTERED
            else -> null
        }
        _state.update { it.copy(notificationProblem = problem) }
    }

    private fun handleReset(id: String) {
        if (id != _state.value.pairId) return
        unpair()
        _state.update { it.copy(pairingWasReset = true) }
    }

    // State

    private fun apply(new: QueueStatus, fromPush: Boolean) {
        val old = _state.value.status
        if (!fromPush) {
            // Backup for a push that didn't arrive. A push already updated the notification itself.
            if (new.state == QueueState.IDLE) {
                QueueNotifier.end(context, lingerAfter(old))
            } else if (new != old) {
                QueueNotifier.show(context, new)
            }
        }
        if (new == old) return
        _state.update { it.copy(status = new) }
        if (new.state == QueueState.FOUND && active && new.isFreshMatch() && flashedFoundAt != new.foundAt) {
            flashedFoundAt = new.foundAt
            _state.update { it.copy(matchAlerts = it.matchAlerts + 1) }
            // A match push that got here first already played the sound.
            if (QueueNotifier.claimMatchAlert(new.foundAt)) alerts.play()
        }
    }

    /** How long "Not in queue" stays up, like the worker's end push. */
    private fun lingerAfter(old: QueueStatus): Long = when {
        old.inMatch -> 10 * 60
        old.state == QueueState.QUEUEING -> 60
        else -> 0
    }

    companion object {
        private const val TAG = "OWQueue"

        @Volatile private var instance: QueueRepository? = null

        fun get(context: Context): QueueRepository =
            instance ?: synchronized(this) { instance ?: QueueRepository(context).also { instance = it } }
    }
}
