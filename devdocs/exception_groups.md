If a coroutine crashes, and all other coroutines cancel gracefully, then by default we raise the original exception directly - without it being wrapped in an exception group of one element.

This is in contrast to e.g. trio's nurseries, which encourage setting a flag to raise a `{Base}ExceptionGroup`, even if it is only containing one element. Presumably this is because it ensures a consistent 'type signature' for the function, in the sense that the raised error type is stable. (As when there are multiple exceptions, then you always get a `{Base}ExceptionGroup`.)

The reason we do not do this by default is because of our particularly simple error semantics: if one coroutine crashes then we cancel all other coroutines. Thus, the 99% use-case is that there really is only one error, and not a collection of multiple potentially-unrelated errors that we want to raise all together.

Correspondingly, we can get multiple errors in two cases only:
- multiple `.run_in_thread`s raise independent errors at the same time.
- when cancelling, a coroutine responds improperly to having a `tinyio.CancelledError` raised within it, and raises some other error.

Both of these are fairly unusual. As such, raising the original error seems generally more useful for most users. It's arguably a little less robust programmatically, but it gives a much more pleasant UX experience for debugging stack traces in CI!
