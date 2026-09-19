package com.tomerady.overwatchqueue

import android.app.Application
import com.tomerady.overwatchqueue.shared.QueueNotifier
import com.tomerady.overwatchqueue.shared.Worker

class OwqApp : Application() {
    override fun onCreate() {
        super.onCreate()
        Worker.baseUrl = BuildConfig.WORKER_URL
        QueueNotifier.createChannels(this)
        QueueRepository.get(this).start()
    }
}
