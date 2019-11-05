# Home Assistant Custom Timer :alarm_clock:
Based on hassio timer from version [0.101.2](https://github.com/home-assistant/home-assistant/releases/tag/0.101.2).

Allows for restoring timer after restart.

## Installing

Create a directory in your Home Assistant `home` directory called `custom_components`. This is the same directory that your `configuration.yaml`file lives. Copy the directory `timer` from this repository (`/custom_components/timer`) to your `custom_components` directory. Home Assistant will now use this code to setup and use your timers.

To add the restore functionality, add the `restore: true` to your timer configuration. If the default 15 minute `restore_timeout` value does not work for your setup, you can change it to whatever time period you wish. See Example below.

## Configuration
Basic configuration is the same as before. To gain the same functionality as the original timer, add the following to your configuration.yaml file:

    # Example configuration.yaml entry
    timer:
      laundry:
        duration: '00:01:00'

### New Restore after restart functionality
To restore the timer after a restart, add the following to your configuration.yaml file:

    # Example configuration.yaml entry
    timer:
      laundry:
        duration: '00:01:00'
        restore: true
        restore_timeout: '00:15:00'  # default timeout is 15 minutes

Setting `restore: true` will enable the timer to be restored at start up. The `restore_timeout` is to control if the `finished` event is triggered if home assistant has been down for longer than your timer was set for.

### Explanation of Behavior:

Say you have the following timer. When the timer is `finished`, an automation is triggered.

    # Example configuration.yaml entry
    timer:
      turn_off_patio_light:
        duration: '01:00:00'
        restore: true
        restore_timeout: '00:15:00'  # default timeout is 15 minutes

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
 
The timer is started with a duration of 1 hour. After 30 minutes have elapsed, there is a power outage and Home Assistant is off for 31 minutes. When Home Assistant comes online, it sees that the timer should have `finished` 1 minute prior to it coming online. Since the `restore_timeout` is set to 15 minutes and only 1 minute has passed since the timer should have `finished`, Home Assistant will fire the event `timer.finished` and the automation will be triggered. The timer will then return to its `idle` state.

_Scenario 3:_
 
The timer is started with a duration of 1 hour. After 50 minutes have elapsed, there is a power outage and Home Assistant is off for 30 minutes. When Home Assistant comes online, it sees that the timer should have `finished` 20 minutes prior to it coming online. Since the `restore_timeout` is set to 15 minutes 20 minutes have passed since the timer should have `finished`, Home Assistant not fire the event and the automation will not be triggered. The timer will then return to its `idle` state.