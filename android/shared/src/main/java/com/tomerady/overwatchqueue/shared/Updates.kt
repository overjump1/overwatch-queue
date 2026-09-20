package com.tomerady.overwatchqueue.shared

import android.app.PendingIntent
import android.content.Context
import android.content.pm.PackageInstaller
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * The app's own updates, from the GitHub releases the Release workflow publishes.
 *
 * The version compared against comes from the APK asset's filename, not the release tag:
 * release.yml only rebuilds the apps that changed and copies the rest of the assets forward, so
 * v2.0.42 can still hold OWQueue-2.0.40.apk.
 */
object Updates {
    private const val RELEASE_API =
        "https://api.github.com/repos/overjump1/overwatch-queue/releases/latest"
    private val ASSET = Regex("""^OWQueue-(.+)\.apk$""", RegexOption.IGNORE_CASE)
    private const val TIMEOUT_MS = 20_000

    /** The APK on offer. [version] is read from its filename. */
    data class Release(val version: String, val url: String, val size: Long)

    /** Shaped like [Worker.Result]: "nothing newer" and "couldn't ask" are not the same answer. */
    sealed interface Result {
        /** [release] is null when this build is already the latest one. */
        data class Ok(val release: Release?) : Result

        data object Failed : Result
    }

    @Serializable
    private data class GithubRelease(val assets: List<GithubAsset> = emptyList())

    @Serializable
    private data class GithubAsset(
        val name: String = "",
        @SerialName("browser_download_url") val url: String = "",
        val size: Long = 0,
    )

    /** `v2.0.42` becomes [2, 0, 42]. A trailing non-number ends it, so `0.0.0-dev` is [0, 0, 0]. */
    fun parse(version: String?): IntArray? {
        val parts = mutableListOf<Int>()
        for (piece in version.orEmpty().trim().trimStart('v', 'V').split('.', '-', '+')) {
            val number = piece.toIntOrNull() ?: break
            parts += number
        }
        if (parts.isEmpty()) return null
        while (parts.size < 3) parts += 0
        return parts.take(3).toIntArray()
    }

    fun isNewer(candidate: String?, than: String?): Boolean {
        val left = parse(candidate) ?: return false
        val right = parse(than) ?: return false
        for (i in 0 until 3) {
            if (left[i] != right[i]) return left[i] > right[i]
        }
        return false
    }

    /**
     * Builds from source and pull request artifacts are 0.x and never check: they're older than
     * every release, so they'd offer an update forever (and couldn't install it, being signed with
     * a different key).
     */
    fun checksForUpdates(current: String?): Boolean = (parse(current)?.get(0) ?: 0) > 0

    /** The APK from a `releases/latest` reply, if it's newer than [current]. */
    fun findUpdate(body: String, current: String?): Release? {
        val release = runCatching {
            QueueStatus.json.decodeFromString(GithubRelease.serializer(), body)
        }.getOrNull() ?: return null
        for (asset in release.assets) {
            val version = ASSET.matchEntire(asset.name)?.groupValues?.get(1) ?: continue
            return if (asset.url.isNotEmpty() && isNewer(version, current)) {
                Release(version, asset.url, asset.size)
            } else {
                null
            }
        }
        return null
    }

    suspend fun latest(current: String?): Result = withContext(Dispatchers.IO) {
        if (!checksForUpdates(current)) return@withContext Result.Ok(null)
        val body = get(RELEASE_API, current) ?: return@withContext Result.Failed
        Result.Ok(findUpdate(body, current))
    }

    private fun get(url: String, current: String?): String? = try {
        val connection = URL(url).openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = TIMEOUT_MS
            connection.readTimeout = TIMEOUT_MS
            connection.setRequestProperty("Accept", "application/vnd.github+json")
            // GitHub turns away requests without one.
            connection.setRequestProperty("User-Agent", "OWQueue/${current.orEmpty()}")
            if (connection.responseCode == 200) {
                connection.inputStream.bufferedReader().use { it.readText() }
            } else {
                null
            }
        } finally {
            connection.disconnect()
        }
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        null
    }

    /**
     * Downloads [release] into the cache, reporting 0..100. Throws if the download is cut short,
     * because a truncated APK is one the installer would reject with nothing useful to say.
     */
    suspend fun download(
        context: Context,
        release: Release,
        onProgress: (Int) -> Unit,
    ): File = withContext(Dispatchers.IO) {
        val directory = File(context.cacheDir, "updates").apply { mkdirs() }
        // Only ever one update in flight, and the old one is no use once a newer release lands.
        directory.listFiles()?.forEach { it.delete() }
        val target = File(directory, "OWQueue-${release.version}.apk")
        val partial = File(directory, target.name + ".part")
        val connection = URL(release.url).openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = TIMEOUT_MS
            connection.readTimeout = TIMEOUT_MS
            connection.setRequestProperty("User-Agent", "OWQueue/${release.version}")
            check(connection.responseCode == 200) { "HTTP ${connection.responseCode}" }
            val total = if (release.size > 0) release.size else connection.contentLength.toLong()
            var written = 0L
            connection.inputStream.use { input ->
                partial.outputStream().use { output ->
                    val buffer = ByteArray(64 * 1024)
                    while (true) {
                        val read = input.read(buffer)
                        if (read < 0) break
                        output.write(buffer, 0, read)
                        written += read
                        if (total > 0) onProgress((written * 100 / total).toInt().coerceAtMost(99))
                    }
                }
            }
            check(release.size <= 0 || written == release.size) {
                "got $written bytes, expected ${release.size}"
            }
        } catch (e: Throwable) {
            partial.delete()
            throw e
        } finally {
            connection.disconnect()
        }
        check(partial.renameTo(target)) { "couldn't move the download into place" }
        onProgress(100)
        target
    }

    /**
     * Hands [apk] to the system installer, which asks the user to confirm. [statusTarget] is the
     * receiver that hears how it went; see UpdateInstallReceiver.
     */
    suspend fun install(context: Context, apk: File, statusTarget: PendingIntent) =
        withContext(Dispatchers.IO) {
            val installer = context.packageManager.packageInstaller
            // Left at the default user-action policy: the system asks for confirmation, except on
            // Android 12+ once this app is the installer of record, where it can skip the prompt.
            val params = PackageInstaller.SessionParams(
                PackageInstaller.SessionParams.MODE_FULL_INSTALL,
            ).apply { setAppPackageName(context.packageName) }
            val sessionId = installer.createSession(params)
            installer.openSession(sessionId).use { session ->
                session.openWrite("apk", 0, apk.length()).use { output ->
                    apk.inputStream().use { it.copyTo(output) }
                    session.fsync(output)
                }
                session.commit(statusTarget.intentSender)
            }
        }
}
