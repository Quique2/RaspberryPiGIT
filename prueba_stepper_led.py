from gpiozero import LED
from time import sleep

STEP_PIN = 17
DIR_PIN = 27

step = LED(STEP_PIN)
direction = LED(DIR_PIN)

while True:
    # Dirección 1
    direction.on()
    print("DIR = HIGH / direccion 1")

    for i in range(20):
        step.on()
        sleep(0.1)
        step.off()
        sleep(0.1)

    sleep(1)

    # Dirección 2
    direction.off()
    print("DIR = LOW / direccion 2")

    for i in range(20):
        step.on()
        sleep(0.1)
        step.off()
        sleep(0.1)

    sleep(1)