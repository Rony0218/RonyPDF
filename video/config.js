// Datos del autor y enlaces que aparecen en el video. Lo que quede en null no aparece.
// Después de cambiar algo, vuelve a renderizar (node render.mjs) para que salga en los MP4.
window.AUTOR = {
  nombre: "Rony Guerrero",
  iniciales: "RG",
  web: "ronyguerrero.com",
  github: "github.com/Rony0218",
  // El repositorio que se le pide a la IA y desde donde se descarga el zip.
  // Ojo: todavía no es público; publícalo antes de subir el video.
  repo: "github.com/Rony0218/RonyPDF",
  linkedin: null,   // por ejemplo "linkedin.com/in/tu-usuario"
  instagram: null,  // por ejemplo "@tu_usuario"
  // Medio para apoyar el proyecto (por ejemplo una página de donaciones). Mientras sea null, el video solo dice
  // «mira el enlace en la descripción» y muestra un aviso genérico.
  apoyo: null,
  version: "2.0",
};
