package com.tomerady.overwatchqueue

import android.content.Context
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
    )

    private val context = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val alerts = MatchAlert(this.context)
    private val _state = MutableStateFlow(UiState(pairId = Pairing.load(this.context)))
    val state: StateFlow<UiState> = _state.asStateFlow()

    @Volatile private var fcmToken: String? = null
    private var active = false
    private var lastPushAt = 0L
    /** The match the screen already flashed for, so the same match never flashes twice. */
    private var flashedFoundAt: Double? = null

    fun start() {
        runCatching {
            FirebaseMessaging.getInstance().token.addOnSuccessListener { setFcmToken(it) }
        }
    }

    /** Polls the worker every 2 seconds until cancelled, while the app is on screen. */
    suspend fun poll() {
        active = true
        try {
            register()
            while (true) {
                refresh()
                delay(2_000)
            }
        } finally {
            active = false
        }
    }

    fun setFcmToken(token: String) {
        if (token == fcmToken) return
        fcmToken = token
        scope.launch { register() }
    }

    /** Returns false if [text] isn't a pairing code. */
    fun pair(text: String): Boolean {
        val id = Pairing.parse(text) ?: return false
        Pairing.save(context, id)
        _state.update { it.copy(pairId = id, pairingWasReset = false, status = QueueStatus.Idle) }
        scope.launch {
            register()
            refresh()
        }
        return true
    }

    fun unpair() {
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
            Worker.Result.Failed -> _state.update { it.copy(reachable = false) }
        }
    }

    private suspend fun register() {
        val id = _state.value.pairId ?: return
        val token = fcmToken ?: return
        val body = buildJsonObject {
            put("kind", "android")
            put("fcm", token)
        }
        if (Worker.register(id, body) == Worker.Result.Reset) handleReset(id)
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
        @Volatile private var instance: QueueRepository? = null

        fun get(context: Context): QueueRepository =
            instance ?: synchronized(this) { instance ?: QueueRepository(context).also { instance = it } }
    }
}
