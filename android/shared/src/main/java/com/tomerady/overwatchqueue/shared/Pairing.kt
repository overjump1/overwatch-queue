package com.tomerady.overwatchqueue.shared

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

    /** Accepts `owq://pair?id=<32 hex>` (from the QR code) and returns the pairing id. */
    fun parse(text: String): String? {
        val uri = runCatching { URI(text.trim()) }.getOrNull() ?: return null
        if (uri.scheme != "owq" || uri.host != "pair") return null
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
