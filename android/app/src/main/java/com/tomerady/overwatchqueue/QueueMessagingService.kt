package com.tomerady.overwatchqueue

import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import com.tomerady.overwatchqueue.shared.QueuePush

/** Gets the worker's pushes, even while the app is closed. */
class QueueMessagingService : FirebaseMessagingService() {
    override fun onNewToken(token: String) {
        QueueRepository.get(applicationContext).setFcmToken(token)
    }

    override fun onMessageReceived(message: RemoteMessage) {
        val push = QueuePush.parse(message.data) ?: return
        QueueRepository.get(applicationContext).onPush(push)
    }
}
