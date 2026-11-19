# Fuentes locales para PDF (#44)

Bitstream Vera 1.10 (regular, bold, italic, bold italic) procede de las fuentes
distribuidas con `reportlab==5.0.1`. Se mantiene `bitstream-vera-license.txt`.
Es la adaptación para papel de la tipografía sans serif del design system.

JetBrains Mono Regular procede de la versión
[v2.304 oficial](https://github.com/JetBrains/JetBrainsMono/tree/v2.304/fonts/ttf),
con licencia SIL Open Font License 1.1 en `OFL.JetBrainsMono.txt`.
SHA256: `a0bf60ef0f83c5ed4d7a75d45838548b1f6873372dfac88f71804491898d138f`.

Los TTF se incluyen sin modificar en la imagen del backend y se embeben como
subconjuntos en cada PDF. No se consulta el sistema operativo ni se descarga
tipografía durante generación/descarga. Se verifican acentos ES/PT en las pruebas.
