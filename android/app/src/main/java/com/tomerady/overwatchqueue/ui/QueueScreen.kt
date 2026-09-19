package com.tomerady.overwatchqueue.ui

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountBalance
import androidx.compose.material.icons.filled.Bedtime
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.EmojiEvents
import androidx.compose.material.icons.filled.HourglassEmpty
import androidx.compose.material.icons.filled.LinkOff
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material.icons.filled.QuestionMark
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.SportsEsports
import androidx.compose.material.icons.filled.SportsScore
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material.icons.filled.WifiOff
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.max
import androidx.compose.ui.unit.sp
import com.tomerady.overwatchqueue.QueueRepository
import com.tomerady.overwatchqueue.shared.GameMode
import com.tomerady.overwatchqueue.shared.QueueState
import com.tomerady.overwatchqueue.shared.QueueStatus
import com.tomerady.overwatchqueue.shared.clockString
import com.tomerady.overwatchqueue.shared.nowSeconds
import kotlinx.coroutines.delay

val Secondary = Color(0x99EBEBF5)
val Orange = Color(0xFFFF9F0A)
private val FlashGreen = Color(QueueStatus.GREEN)

@Composable
fun QueueScreen(state: QueueRepository.UiState, onScan: () -> Unit, onUnpair: () -> Unit) {
    val status = state.status
    val accent = Color(status.accent)
    val glow by animateColorAsState(
        accent.copy(alpha = if (status.state == QueueState.IDLE) 0.08f else 0.28f),
        animationSpec = tween(600),
        label = "glow",
    )
    val flash = remember { Animatable(0f) }
    LaunchedEffect(state.matchAlerts) {
        if (state.matchAlerts == 0) return@LaunchedEffect
        flash.animateTo(0.45f, tween(150))
        delay(250)
        flash.animateTo(0f, tween(900))
    }
    val glowRadius = with(LocalDensity.current) { 420.dp.toPx() }

    Box(
        Modifier
            .fillMaxSize()
            .background(Brush.radialGradient(listOf(glow, Color.Transparent), radius = glowRadius)),
    )
    Box(Modifier.fillMaxSize().background(FlashGreen.copy(alpha = flash.value)))

    Column(
        Modifier.fillMaxSize().systemBarsPadding().padding(16.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            SettingsMenu(onScan, onUnpair)
        }
        Spacer(Modifier.weight(1f))
        ModeBadge(status, 132.dp, Modifier.scale(1f + flash.value * 0.27f))
        Spacer(Modifier.height(18.dp))
        Text(
            status.title,
            fontSize = 34.sp,
            fontWeight = FontWeight.Bold,
            color = if (status.state == QueueState.IDLE) Color.White else accent,
        )
        Text(status.subtitle, fontSize = 20.sp, fontWeight = FontWeight.Medium, color = Secondary)
        if (status.state != QueueState.IDLE) {
            Spacer(Modifier.height(18.dp))
            ElapsedText(
                status,
                TextStyle(
                    fontSize = 76.sp,
                    fontWeight = FontWeight.SemiBold,
                    fontFeatureSettings = "tnum",
                    color = if (status.state == QueueState.FOUND) Secondary else Color.White,
                ),
            )
        }
        when (status.state) {
            QueueState.FOUND -> Text("Head back to your PC", fontSize = 17.sp, fontWeight = FontWeight.SemiBold, color = Secondary)
            QueueState.PLAYING -> Text("${status.modeName} · in match", fontSize = 17.sp, fontWeight = FontWeight.SemiBold, color = Secondary)
            else -> Unit
        }
        Spacer(Modifier.weight(1f))
        if (!state.reachable) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Filled.WifiOff, contentDescription = null, tint = Orange, modifier = Modifier.size(16.dp))
                Spacer(Modifier.width(6.dp))
                Text("Can't reach the server — retrying", fontSize = 13.sp, color = Orange)
            }
        }
    }
}

@Composable
private fun SettingsMenu(onScan: () -> Unit, onUnpair: () -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        IconButton(onClick = { open = true }) {
            Icon(Icons.Filled.Settings, contentDescription = "Settings", tint = Secondary)
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            DropdownMenuItem(
                text = { Text("Scan new code") },
                leadingIcon = { Icon(Icons.Filled.QrCodeScanner, contentDescription = null) },
                onClick = {
                    open = false
                    onScan()
                },
            )
            DropdownMenuItem(
                text = { Text("Unpair", color = Color(0xFFFF453A)) },
                leadingIcon = { Icon(Icons.Filled.LinkOff, contentDescription = null, tint = Color(0xFFFF453A)) },
                onClick = {
                    open = false
                    onUnpair()
                },
            )
        }
    }
}

@Composable
fun ModeBadge(status: QueueStatus, size: Dp, modifier: Modifier = Modifier) {
    val accent = Color(status.accent)
    Box(
        modifier
            .size(size)
            .clip(CircleShape)
            .background(accent.copy(alpha = if (status.state == QueueState.IDLE) 0.18f else 0.25f))
            .border(max(1.5.dp, size / 28), accent, CircleShape),
        contentAlignment = Alignment.Center,
    ) {
        Icon(status.icon, contentDescription = null, tint = accent, modifier = Modifier.size(size * 0.42f))
    }
}

/** Counts up while queueing; shows the final wait once a match is found; counts the match once it's on. */
@Composable
fun ElapsedText(status: QueueStatus, style: TextStyle) {
    val now by produceState(nowSeconds(), status) {
        while (true) {
            value = nowSeconds()
            delay(1_000 - System.currentTimeMillis() % 1_000)
        }
    }
    val text = when (status.state) {
        QueueState.QUEUEING -> status.startedAt?.let { clockString(now - it) } ?: "0:00"
        QueueState.FOUND -> clockString(status.waited ?: 0.0)
        QueueState.PLAYING -> status.foundAt?.let { clockString(now - it) } ?: "–:––"
        QueueState.IDLE -> "–:––"
    }
    Text(text, style = style)
}

private val QueueStatus.icon: ImageVector
    get() = when (state) {
        QueueState.FOUND -> Icons.Filled.Check
        QueueState.QUEUEING -> mode?.icon ?: Icons.Filled.HourglassEmpty
        QueueState.PLAYING -> Icons.Filled.SportsScore
        QueueState.IDLE -> Icons.Filled.Bedtime
    }

private val GameMode.icon: ImageVector
    get() = when (this) {
        GameMode.QUICK_PLAY -> Icons.Filled.Bolt
        GameMode.COMPETITIVE -> Icons.Filled.EmojiEvents
        GameMode.ARCADE -> Icons.Filled.SportsEsports
        GameMode.STADIUM -> Icons.Filled.AccountBalance
        GameMode.MYSTERY_HEROES -> Icons.Filled.QuestionMark
        GameMode.CUSTOM -> Icons.Filled.Tune
    }
