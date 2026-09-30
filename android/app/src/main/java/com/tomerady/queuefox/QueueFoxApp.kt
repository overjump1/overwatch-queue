package com.tomerady.queuefox

import android.app.Application
import com.tomerady.queuefox.shared.Pairing
import com.tomerady.queuefox.shared.QueueNotifier
import com.tomerady.queuefox.shared.Updates
import com.tomerady.queuefox.shared.Worker

class QueueFoxApp : Application() {
    override fun onCreate() {
        super.onCreate()
        Worker.baseUrl = BuildConfig.WORKER_URL
        Updates.releaseApi = BuildConfig.RELEASE_API
        Pairing.schemes = setOf(BuildConfig.PAIR_SCHEME)
        QueueNotifier.createChannels(this)
        QueueRepository.get(this).start()
    }
}
