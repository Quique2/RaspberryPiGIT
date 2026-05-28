import RPi.GPIO as GPIO
from time import sleep

STEP_PIN = 17
DIR_PIN = 27

# Conversión real con tu mesa:
# 180 grados de la mesa ≈ 200 pasos
STEPS_180 = 185

STEP_DELAY = 0.005  # más bajo = más rápido

GPIO.setmode(GPIO.BCM)
GPIO.setup(STEP_PIN, GPIO.OUT)
GPIO.setup(DIR_PIN, GPIO.OUT)

def move_steps(steps, direction):
    GPIO.output(DIR_PIN, direction)
    sleep(0.01)  # pausa para estabilizar DIR

    for _ in range(steps):
        GPIO.output(STEP_PIN, GPIO.HIGH)
        sleep(STEP_DELAY)
        GPIO.output(STEP_PIN, GPIO.LOW)
        sleep(STEP_DELAY)

try:
    while True:
        print("Girando mesa +180 grados")
        move_steps(STEPS_180, GPIO.HIGH)

        sleep(1)

        print("Girando mesa -180 grados")
        move_steps(STEPS_180, GPIO.LOW)

        sleep(1)

except KeyboardInterrupt:
    print("Programa detenido")

finally:
    GPIO.cleanup()