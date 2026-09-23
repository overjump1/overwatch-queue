package com.tomerady.overqueue

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.tomerady.overqueue.ui.PairScreen
import com.tomerady.overqueue.ui.QueueScreen
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    private val repository by lazy { QueueRepository.get(this) }
    private val updater by lazy { Updater.get(this) }

    private val notificationPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) {}

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        if (savedInstanceState == null) handle(intent)
        askForNotifications()

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                // Checks at most every six hours; coming back to the app just gives it the chance.
                updater.check(force = false)
                repository.watch()
            }
        }

        setContent {
            MaterialTheme(colorScheme = darkColorScheme(primary = Color.White)) {
                App(repository, updater)
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handle(intent)
    }

    /** `overqueue://pair?id=...` from the camera app. */
    private fun handle(intent: Intent?) {
        intent?.dataString?.let { repository.pair(it) }
    }

    private fun askForNotifications() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }
}

@Composable
private fun App(repository: QueueRepository, updater: Updater) {
    val state by repository.state.collectAsStateWithLifecycle()
    val update by updater.state.collectAsStateWithLifecycle()
    var scanning by rememberSaveable { mutableStateOf(false) }

    BackHandler(enabled = scanning) { scanning = false }

    Box(Modifier.fillMaxSize().background(Color.Black)) {
        when {
            state.pairId == null -> PairScreen(resetNotice = state.pairingWasReset, onCode = { repository.pair(it) })
            scanning -> PairScreen(
                resetNotice = false,
                onCode = { if (repository.pair(it)) scanning = false },
                onClose = { scanning = false },
            )
            else -> QueueScreen(
                state = state,
                update = update,
                canUpdate = updater.enabled,
                onScan = { scanning = true },
                onUnpair = { repository.unpair() },
                onRefresh = { repository.sync() },
                onCheckForUpdates = { updater.check(force = true) },
                onUpdateTap = { updater.tap() },
            )
        }
    }
}
