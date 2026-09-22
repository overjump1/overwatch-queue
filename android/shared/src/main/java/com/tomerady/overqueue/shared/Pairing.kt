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
     * `owq://` is the same link under the name the app used to go by: a PC that hasn't been
     * updated yet still writes that one, and it has to keep pairing.
     */
    private val SCHEMES = setOf("overqueue", "owq")

    /** Accepts `overqueue://pair?id=<32 hex>` (from the QR code) and returns the pairing id. */
    fun parse(text: String): String? {
        val uri = runCatching { URI(text.trim()) }.getOrNull() ?: return null
        if (uri.scheme !in SCHEMES || uri.host != "pair") return null
        val id = uri.rawQuery.orEmpty()
            .split("&")
            .map { it.split("=", limit = 2) }
            .firstOrNull { it.size == 2 && it[0] == "id" }
            ?.get(1)
            ?.lowercase()
            ?: return null
        return id.takeIf { it.length == 32 && it.all { c -> c in '0'..'9' || c in 'a'..'f' } }
    }
}
