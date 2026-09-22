package com.tomerady.overqueue

import android.app.Application
import com.tomerady.overqueue.shared.QueueNotifier
import com.tomerady.overqueue.shared.Updates
import com.tomerady.overqueue.shared.Worker

class OwqApp : Application() {
    override fun onCreate() {
        super.onCreate()
        Worker.baseUrl = BuildConfig.WORKER_URL
        Updates.releaseApi = BuildConfig.RELEASE_API
        QueueNotifier.createChannels(this)
        QueueRepository.get(this).start()
    }
}
