package com.tomerady.overqueue

import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import com.tomerady.overqueue.shared.QueueNotifier
import com.tomerady.overqueue.shared.QueuePush
import com.tomerady.overqueue.shared.TestPush
import com.tomerady.overqueue.shared.Worker
import kotlinx.coroutines.runBlocking

/** Gets the worker's pushes, even while the app is closed. */
class QueueMessagingService : FirebaseMessagingService() {
    override fun onNewToken(token: String) {
        QueueRepository.get(applicationContext).setFcmToken(token)
    }

    override fun onMessageReceived(message: RemoteMessage) {
        TestPush.parse(message.data)?.let { test ->
            QueueNotifier.showTest(applicationContext)
            // This runs on Firebase's own background thread, which waits for it to return.
            runBlocking { Worker.testArrived(test) }
            return
        }
        val push = QueuePush.parse(message.data) ?: return
        QueueRepository.get(applicationContext).onPush(push)
    }
}
