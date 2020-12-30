# Home Assistant Custom Timer :alarm_clock:
Based on home assistant core timer from version [0.105](https://github.com/home-assistant/home-assistant/releases/).

**NOTE: This version is not compatible with previous versions. But has been tested to work up to 2020.12.2**

Allows for restoring timer after restart. Also compatible with timers > 24 hours.

## Installing

Create a directory in your Home Assistant `home` directory called `custom_components`. This is the same directory that your `configuration.yaml` file lives. Copy the directory `timer` from this repository (`/custom_components/timer`) to your `custom_components` directory. Home Assistant will now use this code to setup and use your timers.

By default, the timer will be restored at HA boot. If the time has passed for when HA should have fired the `finished` event, then the `finished` event for the timer will not be fired. There is an option of adding a grace period to allow this event to fire. 

See Example below.

## Configuration
Basic configuration is the same as before. To load the defaults, add the following to your configuration.yaml file:

    # Example configuration.yaml entry
    timer:
      laundry:

With this configuration, a timer will be added with a 0 `duration` and it will be restored on reboot with the last set duration.

### Disable restore
To revert the timer to the previous functionality, use the following configuration:

    # Example configuration.yaml entry
    timer:
      laundry:
        restore: false


### Utilize the grace period
To allow a timer to fire a `finished` event when the `duration` time has already passed, add the following to your configuration.yaml file:

    # Example configuration.yaml entry
    timer:
      laundry:
        restore_grace_period: '00:15:00'

This sets the `restore_grace_period` to 15 minutes. This means that if HA goes down at 1:00 p.m. and the timer should have `finished` at 1:01 p.m., HA has until 1:16 p.m. to start up and fire the `finished` event. If HA doesn't come back online until 1:17 p.m., then it will not fire the `finished` event and your associated automation will not be triggered.

### More examples:

Say you have the following timer. When the timer is `finished`, an automation is triggered.

    # Example configuration.yaml entry
    timer:
      turn_off_patio_light:
        duration: '01:00:00'
        restore: true
        restore_grace_period: '00:15:00'
    
    # Example automation.yaml entry
    - alias: Timer for patio light finished
      trigger:
      - platform: event
        event_type: timer.finished
        event_data:
          entity_id: timer.turn_off_patio_light
      action:
      - service: switch.turn_off
        entity_id: switch.patio_light

_Scenario 1:_

The timer is started with a duration of 1 hour. After 30 minutes have elapsed, Home Assistant is restarted. Since it only took a few minutes for Home Assistant to restart, your timer continues as expected and there is no difference than had you not restarted Home Assistant.

_Scenario 2:_

The timer is started with a duration of 1 hour. After 30 minutes have elapsed, there is a power outage and Home Assistant is off for 31 minutes. When Home Assistant comes online, it sees that the timer should have `finished` 1 minute prior to it coming online. Since the `restore_grace_period` is set to 15 minutes and only 1 minute has passed since the timer should have `finished`, Home Assistant will fire the event `timer.finished` and the automation will be triggered. The timer will then return to its `idle` state.

_Scenario 3:_

The timer is started with a duration of 1 hour. After 50 minutes have elapsed, there is a power outage and Home Assistant is off for 30 minutes. When Home Assistant comes online, it sees that the timer should have `finished` 20 minutes prior to it coming online. Since the `restore_grace_period` is set to 15 minutes and 20 minutes have passed since the timer should have `finished`, Home Assistant not fire the event and the automation will not be triggered. The timer will then return to its `idle` state.
