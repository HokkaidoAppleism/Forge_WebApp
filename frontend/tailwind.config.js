/**
 * The desktop app's palette, copied from the `tailwind.config` block at the
 * top of electron-app/index.html rather than re-picked by eye. These six
 * colours are what makes ForgeQB look like ForgeQB, and a port that invents
 * its own browns is a different app that happens to do the same thing.
 *
 * The desktop loads Tailwind's runtime JIT from a vendored script and compiles
 * classes in the browser on every launch. Here it is a build step, which is
 * what Tailwind is actually for: the output is a stylesheet containing only
 * the classes this app uses, and no compiler ships to the user.
 */
export default {
  content: ['./index.html', './src/**/*.js'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        'primary-dark': '#1d1816',
        'secondary-dark': '#2c2321',
        'tertiary-dark': '#3e322e',
        'accent-dark': '#584741',
        'text-light': '#efe0db',
        'text-muted': '#baa7a1',
      },
    },
  },
  plugins: [],
}
