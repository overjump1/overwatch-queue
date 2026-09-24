package com.tomerady.overqueue.shared

import android.content.Context
import java.net.URI

object Pairing {
    private const val PREFS = "owqueue"
    private const val KEY = "pairID"

    fun load(context: Context): String? =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, null)

    fun save(context: Context, id: String?) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY, id).apply()
    }

    /**
     * The links this app answers to; the app sets its own at startup (see build.gradle.kts).
     * `owq://` is the same link under the name the app used to go by: a PC that hasn't been
     * updated yet still writes that one, and it has to keep pairing. OverQueue Dev has only
     * `overqueue-dev://`, so it never pairs with the real PC app, or the real app with the dev one.
     */
    var schemes: Set<String> = setOf("overqueue", "owq")

    private val realSchemes = setOf("overqueue", "owq")
    private val devSchemes = setOf("overqueue-dev")

    /** Accepts `overqueue://pair?id=<32 hex>` (from the QR code) and returns the pairing id. */
    fun parse(text: String): String? {
        val uri = runCatching { URI(text.trim()) }.getOrNull() ?: return null
        val scheme = uri.scheme?.lowercase() ?: return null
        if (scheme !in schemes || uri.host != "pair") return null
        val id = uri.rawQuery.orEmpty()
            .split("&")
            .map { it.split("=", limit = 2) }
            .firstOrNull { it.size == 2 && it[0] == "id" }
            ?.get(1)
            ?.lowercase()
            ?: return null
        return id.takeIf { it.length == 32 && it.all { c -> c in '0'..'9' || c in 'a'..'f' } }
    }

    /**
     * Why a scanned code won't pair, to say so on screen rather than ignore it; null for one that
     * does. `appName` is this app's name. Names the link it saw when it's some other app's, so a
     * screenshot shows the mismatch.
     */
    fun rejection(text: String, appName: String): String? {
        if (parse(text) != null) return null
        val uri = runCatching { URI(text.trim()) }.getOrNull()
        val scheme = uri?.scheme?.lowercase()
        if (uri == null || scheme == null || uri.host != "pair") {
            return "That isn't a pairing code. Scan the QR code in $appName on your PC."
        }
        if (scheme in schemes) {
            return "That pairing code is damaged. Press Reset QR code on your PC and scan the new one."
        }
        // The other app's code. Unless this app goes by that name itself, which only a build with
        // the wrong links would, and is exactly what the last message is there to show.
        if (scheme in devSchemes && appName != "OverQueue Dev") {
            return "That code is from OverQueue Dev. Scan the one in $appName on your PC instead."
        }
        if (scheme in realSchemes && appName != "OverQueue") {
            return "That code is from OverQueue. Scan the one in $appName on your PC instead."
        }
        return "That code ($scheme://) isn't one $appName can pair with. " +
            "It takes ${schemes.joinToString(" or ") { "$it://" }}."
    }
}
